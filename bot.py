from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime
from pymongo import MongoClient
from wcwidth import wcswidth
import certifi
import os
import json

# ---------------- MongoDB Setup ----------------
client = None
db = None
votes_collection = None
polls_collection = None

# ---------------- INITIALIZE MONGO AT RUNTIME ----------------
def init_db():
    print("ENV VARS:", {k: v for k, v in os.environ.items() if k in ("MONGO_URI", "BOT_TOKEN")})
    global client, db, votes_collection, polls_collection
    mongo_uri = os.environ.get("MONGO_URI")

    if not mongo_uri:
        raise Exception("❌ MONGO_URI not found. Did you set it in Railway → Variables?")

    client = MongoClient(mongo_uri, tlsCAFile=certifi.where())
    db = client["telegram_bot"]
    votes_collection = db["votes"]
    polls_collection = db["polls"]
    print("✅ MongoDB Connected!")


# ---------------- INITIALIZE BOT TOKEN AT RUNTIME ----------------
def init_bot_token():
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        raise Exception("❌ BOT_TOKEN not found. Did you set it in Railway → Variables?")
    print("✅ Bot Token Loaded!")
    return bot_token

# ---------------- Limits ----------------
MAX_ATTENDEES = 20
MAX_SHADOWS = 2

# Numeric attendee options
ATTENDEE_OPTIONS = {
    "Me": 1,
    "Me +1": 2,
    "Me +2": 3,
    "Me +3": 4
}

# Buttons order
BUTTON_ORDER = list(ATTENDEE_OPTIONS.keys()) + ["Shadow", "Withdraw Vote", "Withdraw Shadow"]

# ---------------- Helpers ----------------
def parse_datetime(input_str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(input_str, fmt)
        except ValueError:
            continue
    return None

def get_poll_buttons(poll_date_str):
    """Return InlineKeyboardMarkup with poll_date embedded for callback"""
    poll_data = votes_collection.find({"poll_date": poll_date_str})
    votes = list(poll_data)

    # Count current totals
    current_attendees = sum(ATTENDEE_OPTIONS.get(v["choice"], 0) for v in votes if v.get("choice") != "Shadow")
    shadow_count = sum(1 for v in votes if v.get("choice") == "Shadow")

    buttons = []
    for opt in BUTTON_ORDER:
        # Remove buttons if max reached
        if opt in ATTENDEE_OPTIONS and current_attendees >= MAX_ATTENDEES:
            continue
        if opt == "Shadow" and shadow_count >= MAX_SHADOWS:
            continue
        buttons.append([InlineKeyboardButton(opt, callback_data=json.dumps({"choice": opt, "poll": poll_date_str}))])
    return InlineKeyboardMarkup(buttons)

# Characters that must be escaped in MarkdownV2
_MD_V2_SPECIAL = r'_*[]()~`>#+-=|{}.!'

def escape_md(text: str) -> str:
    """
    Escape text for Telegram MarkdownV2.
    This inserts a backslash before each special MarkdownV2 character.
    """
    if text is None:
        return ""
    s = str(text)
    # Order matters for backslash itself; replace backslash first
    s = s.replace("\\", "\\\\")
    for ch in _MD_V2_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    return s

def truncate_to_width(s: str, width: int) -> str:
    """Truncate string s so that its display width (wcswidth) <= width."""
    if width <= 0:
        return ""
    if wcswidth(s) <= width:
        return s
    out = ""
    cur = 0
    for ch in s:
        w = wcswidth(ch)
        if cur + w > width:
            break
        out += ch
        cur += w
    return out

def _pad(s: str, width: int, align: str = "left") -> str:
    """
    Pad/truncate based on display width (wcswidth).
    Return raw (unescaped) padded string — escaping is done later when assembling the final line.
    """
    s = "" if s is None else str(s)
    # Ensure truncated to width if needed
    s = truncate_to_width(s, width)
    cur_w = wcswidth(s)
    diff = width - cur_w
    if diff <= 0:
        return s
    if align == "right":
        return " " * diff + s
    else:
        return s + " " * diff

def get_poll_data(poll_date_str):
    votes = list(votes_collection.find({"poll_date": poll_date_str}))
    shadows = [v for v in votes if v.get("choice") == "Shadow"]
    attendee_votes = [v for v in votes if v.get("choice") != "Shadow"]

    attendees = []
    waitlist = []
    extended_attendees = []

    total = 0
    for v in attendee_votes:
        pax = v["count"]
        if total < 10:
            if total + pax <= 10:
                attendees.append(v)
                total += pax
            else:
                waitlist.append(v)
        elif total < 14:
            waitlist.append(v)
            total += pax
        else:
            extended_attendees.append(v)

    if total >= 14:
        attendees = attendees + waitlist + extended_attendees
        waitlist = []

    return {"attendees": attendees, "waitlist": waitlist, "shadows": shadows}

def _make_attendee_table(attendees, max_attendees):
    """
    Build an aligned plain-text table (no code block). Uses spaces for column separation so we
    don't need '|' or '-' characters (those require escaping).
    Columns:
      Name (left), Reaction Time (left), Pax (right)
    """
    # compute column widths using raw (unescaped) content
    name_w = max(4, max((wcswidth(v["user"]) for v in attendees), default=0))
    time_w = 16  # 'YYYY-MM-DD HH:MM' visible width
    pax_w = 3

    lines = []
    # header (raw)
    header = f"{_pad('Name', name_w)}  {_pad('Reaction Time', time_w)}  {_pad('Pax', pax_w, 'right')}"
    # underline (use spaces and dashes as visual cue but dashes will be escaped when assembled)
    underline = f"{'-'*name_w}  {'-'*time_w}  {'-'*pax_w}"

    # escape header and underline for MarkdownV2
    lines.append(escape_md(header))
    lines.append(escape_md(underline))

    total = 0
    for v in attendees:
        name_raw = _pad(v["user"], name_w)
        time_raw = _pad(v["time"].strftime("%Y-%m-%d %H:%M"), time_w)
        pax_raw = _pad(str(v["count"]), pax_w, "right")
        line_raw = f"{name_raw}  {time_raw}  {pax_raw}"
        lines.append(escape_md(line_raw))
        total += v["count"]

    # determine maximum shown (preserve your existing logic)
    maximum = 7
    if total > 7:
        maximum = 10
    elif total > 14:
        maximum = 20

    lines.append(escape_md(f"Total Attending: {total}/{maximum}"))
    return "\n".join(lines)

def _make_shadow_table(shadows, max_shadows):
    name_w = max(7, max((wcswidth(v["user"]) for v in shadows), default=0))
    time_w = 16

    lines = []
    header = f"{_pad('Shadows', name_w)}  {_pad('Reaction Time', time_w)}"
    underline = f"{'-'*name_w}  {'-'*time_w}"

    lines.append(escape_md(header))
    lines.append(escape_md(underline))

    for v in shadows:
        name_raw = _pad(v["user"], name_w)
        time_raw = _pad(v["time"].strftime("%Y-%m-%d %H:%M"), time_w)
        lines.append(escape_md(f"{name_raw}  {time_raw}"))

    lines.append(escape_md(f"Total Shadows: {len(shadows)}/{max_shadows}"))
    return "\n".join(lines)

def build_poll_text(poll_date_str, poll_data):
    attendees = poll_data.get("attendees", [])
    waitlist = poll_data.get("waitlist", [])
    shadows = poll_data.get("shadows", [])

    parts = []
    # Title: escape the poll date but keep emoji raw
    parts.append(f"📅 Poll for {escape_md(poll_date_str)}")

    # Attendees table
    if attendees:
        att_table = _make_attendee_table(attendees, MAX_ATTENDEES)
        parts.append(att_table)

    # Waitlist table
    if waitlist:
        wait_table = _make_attendee_table(waitlist, MAX_ATTENDEES)
        parts.append(f"*Waitlist:*")  # bold-ish (asterisks) will be recognized in MarkdownV2
        parts.append(wait_table)

    # Shadows table
    if shadows:
        sh_table = _make_shadow_table(shadows, MAX_SHADOWS)
        parts.append(sh_table)
    else:
        parts.append("_No shadows yet._")  # italic

    parts.append("⚡️ Use the buttons below to vote or withdraw.")
    # Join with blank line for readability
    return "\n\n".join(parts)

# ---------------- Handlers ----------------
async def start_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        instructions = (
            "📅 *How to start a poll*\n\n"
            "Use one of the following formats:\n"
            "`/poll YYYY-MM-DD`\n"
            "`/poll YYYY-MM-DD HH:MM`\n"
            "`/poll YYYY-MM-DD HH:MM:SS`\n\n"
            "Example:\n"
            "`/poll 2025-09-20 19:30`"
        )
        await update.message.reply_text(instructions, parse_mode="MarkdownV2")
        return

    input_str = " ".join(args)
    parsed_dt = parse_datetime(input_str)
    if not parsed_dt:
        await update.message.reply_text("❌ Invalid date/time format.", parse_mode="MarkdownV2")
        return

    poll_date_str = parsed_dt.strftime("%Y-%m-%d")

    if polls_collection.find_one({"poll_date": poll_date_str}):
        await update.message.reply_text(f"⚠️ A poll for {escape_md(poll_date_str)} already exists!", parse_mode="MarkdownV2")
        return

    polls_collection.insert_one({
        "poll_date": poll_date_str,
        "creator": update.effective_user.username,
        "time": datetime.now()
    })
    votes_collection.delete_many({"poll_date": poll_date_str})

    question = f"Attending ({escape_md(poll_date_str)}) session"
    await update.message.reply_text(question, reply_markup=get_poll_buttons(poll_date_str), parse_mode="MarkdownV2")
    print(f"[INFO] Poll started by {update.effective_user.username} for {poll_date_str}")

async def safe_edit_message(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )
    except Exception as e:
        # Only ignore "Message is not modified" errors
        if "Message is not modified" not in str(e):
            print(f"[WARN] Could not edit message: {e}")

async def vote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        data = json.loads(query.data)
        choice = data["choice"]
        poll_date_str = data["poll"]
    except Exception:
        await query.answer("❌ Invalid callback data.", show_alert=True)
        return

    user = query.from_user.username or query.from_user.first_name
    poll_votes = list(votes_collection.find({"poll_date": poll_date_str}))
    existing = next((v for v in poll_votes if v["user"] == user), None)

    # Withdraw logic
    if choice.startswith("Withdraw"):
        if not existing:
            await query.answer("No vote to withdraw!", show_alert=True)
            return
        votes_collection.delete_many({"user": user, "poll_date": poll_date_str})
        await query.answer("Your vote has been withdrawn.")
    else:
        # New vote logic
        count = ATTENDEE_OPTIONS.get(choice, 0)
        if choice == "Shadow":
            if any(v["user"] == user and v["choice"] == "Shadow" for v in poll_votes):
                await query.answer("Already a shadow!", show_alert=True)
                return
            votes_collection.insert_one({
                "user": user,
                "choice": "Shadow",
                "count": 0,
                "time": datetime.now(),
                "poll_date": poll_date_str
            })
            await query.answer("You are now a Shadow.")
        else:
            current_total = sum(v["count"] for v in poll_votes if v.get("choice") != "Shadow")
            if current_total + count > 20:
                await query.answer("❌ Total attendees limit reached!", show_alert=True)
                return
            if existing and existing.get("choice") != "Shadow":
                votes_collection.delete_one({"_id": existing["_id"]})
            votes_collection.insert_one({
                "user": user,
                "choice": choice,
                "count": count,
                "time": datetime.now(),
                "poll_date": poll_date_str
            })
            await query.answer(f"You voted {escape_md(choice)}.")

    # Refresh display
    poll_data = get_poll_data(poll_date_str)
    display_text = build_poll_text(poll_date_str, poll_data)
    await safe_edit_message(query.message, display_text, reply_markup=get_poll_buttons(poll_date_str))

# ---------------- Main ----------------
if __name__ == "__main__":
    init_db()  # connect to Mongo at runtime
    bot_token = init_bot_token()  # load Token

    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CommandHandler("poll", start_poll))
    app.add_handler(CallbackQueryHandler(vote_handler))

    async def on_shutdown(application):
        print("\nBot stopped gracefully.")

    app.post_stop = on_shutdown
    print("Bot is running...")
    app.run_polling()
