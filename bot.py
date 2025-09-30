import os
import json
from datetime import datetime, timezone
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from wcwidth import wcswidth

# ---------------- Constants ----------------
MAX_ATTENDEES = 20
MAX_SHADOWS = 2

ATTENDEE_OPTIONS = {
    "Me": 1,
    "Me +1": 2,
    "Me +2": 3,
    "Me +3": 4
}

BUTTON_ORDER = list(ATTENDEE_OPTIONS.keys()) + ["Shadow", "Withdraw Vote", "Withdraw Shadow"]

# ---------------- MongoDB ----------------
client = None
db = None
votes_collection = None
polls_collection = None

def init_db():
    global client, db, votes_collection, polls_collection
    mongo_uri = os.environ.get("MONGO_URI")
    if not mongo_uri:
        raise Exception("❌ MONGO_URI not found.")
    client = MongoClient(mongo_uri)
    db = client["telegram_bot"]
    votes_collection = db["votes"]
    polls_collection = db["polls"]
    print("✅ MongoDB Connected!")

def init_bot_token():
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        raise Exception("❌ BOT_TOKEN not found.")
    print("✅ Bot Token Loaded!")
    return bot_token

# ---------------- Markdown V2 Escaping ----------------
_MD_V2_SPECIAL = r'_*[]()~`>#+-=|{}.!'

def escape_md_v2(text: str) -> str:
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\")
    for ch in _MD_V2_SPECIAL:
        s = s.replace(ch, "\\" + ch)
    return s

# ---------------- Table Helpers ----------------
def truncate_to_width(s: str, width: int) -> str:
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
    s = "" if s is None else str(s)
    s = truncate_to_width(s, width)
    cur_w = wcswidth(s)
    diff = width - cur_w
    if diff <= 0:
        return s
    return (" " * diff + s) if align == "right" else (s + " " * diff)

def _make_attendee_table(attendees, max_attendees):
    if not attendees:
        return escape_md_v2("_No attendees yet._")

    name_w = max(4, max(len(f"{v['user']} ({v['choice']})") for v in attendees))
    time_w = 16
    pax_w = 3

    header = f"{_pad('Name', (name_w+10))} | {_pad('Reaction Time', (time_w+10))} | {_pad('Pax', pax_w, 'right')}"
    underline = f"{'-'*(name_w+15)}-+-{'-'*(time_w+15)}-+-{'-'*pax_w}"
    raw_lines = [header, underline]

    total = 0
    for v in attendees:
        formatted_time = v['time'].strftime('%Y-%m-%d %H:%M')
        name_with_choice = f"{v['user']} ({v['choice']})"
        line = f"{_pad(name_with_choice, name_w)} | {_pad(formatted_time, time_w)} | {_pad(v['count'], pax_w, 'right')}"
        raw_lines.append(line)  # ✅ Append each line
        total += v['count']

    maximum = 10 if total <= 14 else 20
    raw_lines.append(f"Total Attending: {total}/{maximum}")

    return "\n".join([escape_md_v2(l) for l in raw_lines])


def _make_shadow_table(shadows, max_shadows):
    if not shadows:
        return escape_md_v2("_No shadows yet._")

    name_w = max(4, max(len(v["user"]) for v in shadows))
    time_w = 16

    header = f"{_pad('Name', (name_w+10))} | {_pad('Reaction Time', (time_w+10))}"
    underline = f"{'-'*(name_w+15)}-+-{'-'*(time_w+15)}"
    raw_lines = [header, underline]

    for v in shadows:
        formatted_time = v['time'].strftime('%Y-%m-%d %H:%M')
        line = f"{_pad(v['user'], name_w)} | {_pad(formatted_time, time_w)}"
        raw_lines.append(line)  # ✅ Append each line

    raw_lines.append(f"Total Shadows: {len(shadows)}/{max_shadows}")
    return "\n".join([escape_md_v2(l) for l in raw_lines])


# ---------------- Poll Data ----------------
def parse_datetime(input_str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(input_str, fmt)
        except ValueError:
            continue
    return None

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

# ---------------- Poll Buttons ----------------
def get_poll_buttons(poll_date_str):
    poll_data = votes_collection.find({"poll_date": poll_date_str})
    votes = list(poll_data)
    current_attendees = sum(ATTENDEE_OPTIONS.get(v["choice"], 0) for v in votes if v.get("choice") != "Shadow")
    shadow_count = sum(1 for v in votes if v.get("choice") == "Shadow")

    buttons = []
    for opt in BUTTON_ORDER:
        if opt in ATTENDEE_OPTIONS and current_attendees >= MAX_ATTENDEES:
            continue
        if opt == "Shadow" and shadow_count >= MAX_SHADOWS:
            continue
        buttons.append([InlineKeyboardButton(opt, callback_data=json.dumps({"choice": opt, "poll": poll_date_str}))])
    return InlineKeyboardMarkup(buttons)

# ---------------- Build Poll Text ----------------
def build_poll_text(poll_date_str, poll_data):
    attendees = poll_data.get("attendees", [])
    waitlist = poll_data.get("waitlist", [])
    shadows = poll_data.get("shadows", [])

    parts = [escape_md_v2(f"📅 Poll for {poll_date_str}"), ""]

    if attendees:
        parts.append(_make_attendee_table(attendees, MAX_ATTENDEES))
        parts.append("")

    if waitlist:
        parts.append(escape_md_v2("📥 Waitlist"))
        parts.append("")
        parts.append(_make_attendee_table(waitlist, MAX_ATTENDEES))
        parts.append("")

    if shadows:
        parts.append(escape_md_v2("👥 Shadows"))
        parts.append("")
        parts.append(_make_shadow_table(shadows, MAX_SHADOWS))
        parts.append("")
    else:
        parts.append(escape_md_v2("_No shadows yet._"))
        parts.append("")

    parts.append(escape_md_v2("⚡️ Use the buttons below to vote or withdraw."))
    return "\n".join(parts)

# ---------------- Bot Handlers ----------------
async def start_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        instructions = (
            "📅 How to start a poll:\n\n"
            "Use one of these formats:\n"
            "/poll YYYY-MM-DD\n"
            "/poll YYYY-MM-DD HH:MM\n"
            "/poll YYYY-MM-DD HH:MM:SS\n\n"
            "Example: /poll 2025-09-20 19:30"
        )
        await update.message.reply_text(escape_md_v2(instructions), parse_mode="MarkdownV2")
        return

    input_str = " ".join(args)
    parsed_dt = parse_datetime(input_str)
    if not parsed_dt:
        await update.message.reply_text("❌ Invalid date/time format.", parse_mode="MarkdownV2")
        return

    poll_date_str = parsed_dt.strftime("%Y-%m-%d")
    if polls_collection.find_one({"poll_date": poll_date_str}):
        await update.message.reply_text(f"⚠️ A poll for {poll_date_str} already exists!", parse_mode="MarkdownV2")
        return

    polls_collection.insert_one({
        "poll_date": poll_date_str,
        "creator": update.effective_user.username,
        "time": datetime.now()
    })
    votes_collection.delete_many({"poll_date": poll_date_str})
    question = f"Attending ({poll_date_str}) session"
    await update.message.reply_text(escape_md_v2(question), reply_markup=get_poll_buttons(poll_date_str))

async def safe_edit_message(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
    except Exception as e:
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

    # Withdraw
    if choice.startswith("Withdraw"):
        if not existing:
            await query.answer("No vote to withdraw!", show_alert=True)
            return
        votes_collection.delete_many({"user": user, "poll_date": poll_date_str})
        await query.answer("Your vote has been withdrawn.")
    else:
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
            if current_total + count > MAX_ATTENDEES:
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
            await query.answer(f"You voted {choice}.")

    # Refresh display
    poll_data = get_poll_data(poll_date_str)
    display_text = build_poll_text(poll_date_str, poll_data)
    await safe_edit_message(query.message, display_text, reply_markup=get_poll_buttons(poll_date_str))

# ---------------- Main ----------------
if __name__ == "__main__":
    init_db()
    bot_token = init_bot_token()

    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CommandHandler("poll", start_poll))
    app.add_handler(CallbackQueryHandler(vote_handler))

    async def on_shutdown(application):
        print("\nBot stopped gracefully.")

    app.post_stop = on_shutdown
    print("Bot is running...")
    app.run_polling()








