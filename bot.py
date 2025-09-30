from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from datetime import datetime
from pymongo import MongoClient
from wcwidth import wcswidth
import certifi
import os
import config
import json

# ---------------- MongoDB Setup ----------------
# Global placeholders (not initialized at import time!)
client = None
db = None
votes_collection = None
polls_collection = None

# ---------------- INITIALIZE MONGO AT RUNTIME ----------------
def init_db():
    print("ENV VARS:", os.environ)
    global client, db, votes_collection, polls_collection
    mongo_uri = os.environ.get("MONGO_URI")

    if not mongo_uri:
        raise Exception("❌ MONGO_URI not found. Did you set it in Railway → Variables?")

    client = MongoClient(mongo_uri)
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

def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _pad(s: str, width: int, align: str = "left") -> str:
    s = str(s)
    diff = width - len(s)
    if diff <= 0:
        return s[:width]
    return (s + " " * diff) if align == "left" else (" " * diff + s)

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
    name_w = max(4, max((len(v["user"]) for v in attendees), default=0))
    time_w = 16  # 'YYYY-MM-DD HH:MM'
    pax_w = 3

    lines = []
    lines.append(f"{_pad('Name', name_w)} | {_pad('Reaction Time', time_w)} | {_pad('Pax', pax_w, 'right')}")
    lines.append(f"{'-'*name_w}-+-{'-'*time_w}-+-{'-'*pax_w}")

    total = 0
    for v in attendees:
        lines.append(
            f"{_pad(v['user'], name_w)} | {_pad(v['time'].strftime('%Y-%m-%d %H:%M'), time_w)} | {_pad(v['count'], pax_w, 'right')}"
        )
        total += v['count']

    lines.append(f"Total Attending: {total}/{max_attendees}")
    return "\n".join(lines)

def _make_shadow_table(shadows, max_shadows):
    name_w = max(7, max((len(v["user"]) for v in shadows), default=0))
    time_w = 16

    lines = []
    lines.append(f"{_pad('Shadows', name_w)} | {_pad('Reaction Time', time_w)}")
    lines.append(f"{'-'*name_w}-+-{'-'*time_w}")

    for v in shadows:
        lines.append(f"{_pad(v['user'], name_w)} | {_pad(v['time'].strftime('%Y-%m-%d %H:%M'), time_w)}")

    lines.append(f"Total Shadows: {len(shadows)}/{max_shadows}")
    return "\n".join(lines)

def build_poll_text(poll_date_str, poll_data):
    attendees = poll_data.get("attendees", [])
    waitlist = poll_data.get("waitlist", [])
    shadows = poll_data.get("shadows", [])

    parts = [f"📅 Poll for {_escape_html(poll_date_str)}"]

    # Attendees table
    if attendees:
        att_table = _make_attendee_table(attendees, MAX_ATTENDEES)
        parts.append(f"<pre>{_escape_html(att_table)}</pre>")

    # Waitlist table
    if waitlist:
        wait_table = _make_attendee_table(waitlist, MAX_ATTENDEES)
        parts.append("<b>Waitlist:</b>")
        parts.append(f"<pre>{_escape_html(wait_table)}</pre>")

    # Shadows table
    if shadows:
        sh_table = _make_shadow_table(shadows, MAX_SHADOWS)
        parts.append(f"<pre>{_escape_html(sh_table)}</pre>")
    else:
        parts.append("<i>No shadows yet.</i>")

    parts.append("⚡️ Use the buttons below to vote or withdraw.")
    return "\n\n".join(parts)

# ---------------- Handlers ----------------
async def start_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        instructions = (
            "📅 *How to start a poll:*\n\n"
            "Use `/poll YYYY-MM-DD` or `/poll YYYY-MM-DD HH:MM` or `/poll YYYY-MM-DD HH:MM:SS`\n"
            "Example: `/poll 2025-09-20 19:30`"
        )
        await update.message.reply_text(instructions, parse_mode="HTML")
        return

    input_str = " ".join(args)
    parsed_dt = parse_datetime(input_str)
    if not parsed_dt:
        await update.message.reply_text("❌ Invalid date/time format.")
        return

    poll_date_str = parsed_dt.strftime("%Y-%m-%d")

    if polls_collection.find_one({"poll_date": poll_date_str}):
        await update.message.reply_text(f"⚠️ A poll for {poll_date_str} already exists!")
        return

    polls_collection.insert_one({
        "poll_date": poll_date_str,
        "creator": update.effective_user.username,
        "time": datetime.now()
    })
    votes_collection.delete_many({"poll_date": poll_date_str})

    question = f"Attending ({poll_date_str}) session"
    await update.message.reply_text(question, reply_markup=get_poll_buttons(poll_date_str))
    print(f"[INFO] Poll started by {update.effective_user.username} for {poll_date_str}")

async def safe_edit_message(message, text, reply_markup=None):
    try:
        await message.edit_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
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
            await query.answer(f"You voted {choice}.")

    # Refresh display
    poll_data = get_poll_data(poll_date_str)
    display_text = build_poll_text(poll_date_str, poll_data)
    await safe_edit_message(query.message, display_text, reply_markup=get_poll_buttons(poll_date_str))
# ---------------- Main ----------------
if __name__ == "__main__":
    init_db()  # ✅ Connect to Mongo **only now**
    bot_token = init_bot_token()  # ✅ Load Token
    
    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(CommandHandler("poll", start_poll))
    app.add_handler(CallbackQueryHandler(vote_handler))

    async def on_shutdown(application):
        print("\nBot stopped gracefully.")

    app.post_stop = on_shutdown
    print("Bot is running...")
    app.run_polling()




