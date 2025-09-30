from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient
from datetime import datetime
import logging
import config

# ------------------- Logging Setup -------------------
logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)
fh = logging.FileHandler("bot_activity.log")
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
fh.setFormatter(formatter)
logger.addHandler(fh)

def log_action(user, action, poll_date, error=None):
    """Log a user's action or error"""
    msg = f"User: {user} | Action: {action} | Poll: {poll_date}"
    if error:
        logger.error(f"{msg} | ERROR: {error}")
    else:
        logger.info(msg)

# ------------------- MongoDB Setup -------------------
client = MongoClient(config.MONGO_URI)
db = client["telegram_bot"]
votes_collection = db["votes"]
polls_collection = db["polls"]

# ------------------- Limits -------------------
MAX_ATTENDEES = 10
MAX_SHADOWS = 2

ATTENDEE_OPTIONS = {
    "Me": 1,
    "Me +1": 2,
    "Me +2": 3,
    "Me +3": 4
}

BUTTON_ORDER = list(ATTENDEE_OPTIONS.keys()) + ["Shadow", "Withdraw"]

# ------------------- Helpers -------------------
def parse_datetime(input_str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(input_str, fmt)
        except ValueError:
            continue
    return None

def build_poll_text(poll_date_str):
    attendees_cursor = votes_collection.find(
        {"poll_date": poll_date_str, "choice": {"$ne": "Shadow"}}
    ).sort("time", 1)
    attendees = list(attendees_cursor)
    total_attendees = sum(ATTENDEE_OPTIONS[v["choice"]] for v in attendees) if attendees else 0

    lines = []
    for v in attendees:
        time_str = v["time"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{v['user']} → {v['choice']} ({v['count']}) at {time_str}")

    lines.append(f"\nTotal going: {total_attendees}/{MAX_ATTENDEES}")

    shadow_cursor = votes_collection.find({"poll_date": poll_date_str, "choice": "Shadow"}).sort("time", 1)
    shadows = list(shadow_cursor)
    if shadows:
        lines.append("\n--- Shadows ---")
        for v in shadows:
            time_str = v["time"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"{v['user']} → Shadow at {time_str}")
        lines.append(f"Shadows: {len(shadows)}/{MAX_SHADOWS}")

    return f"Attending ({poll_date_str}) session\n\n" + "\n".join(lines)

def get_poll_buttons():
    return InlineKeyboardMarkup([[InlineKeyboardButton(opt, callback_data=opt)] for opt in BUTTON_ORDER])

# ------------------- Handlers -------------------
async def start_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user = update.effective_user.username or update.effective_user.first_name

    if not args:
        instructions = (
            "📅 *How to start a poll:*\n\n"
            "Use `/poll YYYY-MM-DD [HH:MM[:SS]]`\n"
            "Example: `/poll 2025-09-20 19:30`"
        )
        await update.message.reply_text(instructions, parse_mode="Markdown")
        return

    input_str = " ".join(args)
    parsed_dt = parse_datetime(input_str)
    if not parsed_dt:
        await update.message.reply_text("❌ Invalid date/time format!")
        log_action(user, f"Attempted to start poll with invalid date '{input_str}'", poll_date=None, error="Invalid date")
        return

    poll_date_str = parsed_dt.strftime("%Y-%m-%d")

    if polls_collection.find_one({"poll_date": poll_date_str}):
        await update.message.reply_text(f"A poll for {poll_date_str} already exists!")
        log_action(user, "Attempted to start duplicate poll", poll_date_str, error="Duplicate poll")
        return

    polls_collection.insert_one({
        "poll_date": poll_date_str,
        "creator": user,
        "time": datetime.now().astimezone()
    })

    votes_collection.delete_many({"poll_date": poll_date_str})
    await update.message.reply_text(f"Attending ({poll_date_str}) session", reply_markup=get_poll_buttons())
    log_action(user, "Started poll", poll_date_str)

async def vote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    user = query.from_user.username or query.from_user.first_name

    try:
        poll_date_str = query.message.text.split("(")[1].split(")")[0]
    except Exception as e:
        await query.answer("Error: Poll date missing.", show_alert=True)
        log_action(user, f"Clicked {choice}", poll_date=None, error="Poll date missing")
        return

    try:
        if choice == "Withdraw":
            existing = votes_collection.find_one({"user": user, "poll_date": poll_date_str})
            if not existing:
                await query.answer("You haven't voted yet!", show_alert=True)
                log_action(user, "Withdraw attempt", poll_date_str, error="No existing vote")
                return

            votes_collection.delete_many({"user": user, "poll_date": poll_date_str})
            log_action(user, "Withdrew vote", poll_date_str)
        
        elif choice == "Shadow":
            shadow_count = votes_collection.count_documents({"poll_date": poll_date_str, "choice": "Shadow"})
            existing_shadow = votes_collection.find_one({"user": user, "poll_date": poll_date_str, "choice": "Shadow"})
            
            if existing_shadow:
                await query.answer("You already picked Shadow!", show_alert=True)
                log_action(user, "Shadow attempt", poll_date_str, error="Already Shadow")
            elif shadow_count >= MAX_SHADOWS:
                await query.answer("Shadow slots are full!", show_alert=True)
                log_action(user, "Shadow attempt", poll_date_str, error="Shadow slots full")
            else:
                votes_collection.delete_many({"user": user, "poll_date": poll_date_str})
                votes_collection.insert_one({
                    "user": user,
                    "choice": "Shadow",
                    "count": 0,
                    "time": datetime.now().astimezone(),
                    "poll_date": poll_date_str
                })
                await query.answer("You are now a Shadow.")
                log_action(user, "Joined as Shadow", poll_date_str)

        elif choice in ATTENDEE_OPTIONS:
            poll_votes = list(votes_collection.find({"poll_date": poll_date_str}))
            existing_vote = next((v for v in poll_votes if v["user"] == user), None)
            current_attendees = sum(ATTENDEE_OPTIONS[v["choice"]] for v in poll_votes if v["choice"] != "Shadow")
            if existing_vote and existing_vote["choice"] != "Shadow":
                current_attendees -= ATTENDEE_OPTIONS.get(existing_vote["choice"], 0)

            if current_attendees + ATTENDEE_OPTIONS[choice] > MAX_ATTENDEES:
                await query.answer("❌ Cannot add: total attendees limit reached!", show_alert=True)
                log_action(user, f"Attempted to vote {choice}", poll_date_str, error="Attendee limit reached")
                display_text = build_poll_text(poll_date_str)
                await query.message.edit_text(text=display_text, reply_markup=get_poll_buttons())
                return

            votes_collection.delete_many({"user": user, "poll_date": poll_date_str})
            votes_collection.insert_one({
                "user": user,
                "choice": choice,
                "count": ATTENDEE_OPTIONS[choice],
                "time": datetime.now().astimezone(),
                "poll_date": poll_date_str
            })
            await query.answer(f"You voted {choice}.")
            log_action(user, f"Voted {choice}", poll_date_str)

        else:
            await query.answer("Invalid option.", show_alert=True)
            log_action(user, f"Clicked {choice}", poll_date_str, error="Unknown option")

    except Exception as e:
        await query.answer("An error occurred.", show_alert=True)
        log_action(user, f"Clicked {choice}", poll_date_str, error=str(e))

    # Always refresh display
    display_text = build_poll_text(poll_date_str)
    await query.message.edit_text(text=display_text, reply_markup=get_poll_buttons())

# ------------------- Main -------------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("poll", start_poll))
    app.add_handler(CallbackQueryHandler(vote_handler))

    print("Bot is running...")
    app.run_polling()
