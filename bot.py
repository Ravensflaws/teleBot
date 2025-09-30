import os
import json
from datetime import datetime
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
_MD_V2_SPECIAL = r'_*[]()~>#+-=|{}.!'

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
    name_w = max(4, max((wcswidth(f"{v['user']} ({v['choice']})") for v in attendees), default=0))
    time_w = 16
    pax_w = 3

    lines = []
    header = f"{_pad('Name', name_w+10)} | {_pad('Reaction Time', time_w+10)} | {_pad('Pax', pax_w, 'right')}"
    underline = f"{'-'*(name_w+15)}-+-{'-'*(time_w+15)}-+-{'-'*pax_w}"
    lines.extend([header, underline])

    total = 0
    for v in attendees:
        # Display MongoDB time as-is
        formatted_time = str(v['time'])[:16].replace('T', ' ')
        name_with_choice = f"{v['user']} ({v['choice']})"
        line = f"{_pad(name_with_choice, name_w+10)} | {_pad(formatted_time, time_w+10)} | {_pad(v['count'], pax_w, 'right')}"
        lines.append(line)
        total += v['count']

    maximum = 10 if total <= 14 else 20
    lines.append(f"Total Attending: {total}/{maximum}")

    return "\n".join([escape_md_v2(l) for l in lines])

def _make_shadow_table(shadows, max_shadows):
    if not shadows:
        return escape_md_v2("_No shadows yet._")

    name_w = max(7, max((wcswidth(v["user"]) for v in shadows), default=0))
    time_w = 16

    lines = []
    header = f"{_pad('Name', name_w+10)} | {_pad('Reaction Time', time_w+10)}"
    underline = f"{'-'*(name_w+15)}-+-{'-'*(time_w+15)}"
    lines.extend([header, underline])

    for v in shadows:
        formatted_time = str(v['time'])[:16].replace('T', ' ')
        line = f"{_pad(v['user'], name_w+10)} | {_pad(formatted_time, time_w+10)}"
        lines.append(line)

    lines.append(f"Total Shadows: {len(shadows)}/{max_shadows}")
    return "\n".join([escape_md_v2(l) for l in lines])

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
    votes = list(votes_collection.find({"poll_date": poll_date_str}))
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
    if not parsed
