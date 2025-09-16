# from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
# from pymongo import MongoClient
# from datetime import datetime, timedelta
# import config

# # ---------------- MongoDB Setup ----------------
# client = MongoClient(config.MONGO_URI)
# db = client["telegram_bot"]
# votes_collection = db["votes"]
# polls_collection = db["polls"]

# client.drop_database("telegram_bot")

# MAX_ATTENDEES = 10

# # Options with counts
# OPTIONS = {
#     "Me": 1,
#     "Me +1": 2,
#     "Me +2": 3,
#     "Me +3": 4
# }

# # ---------------- Helpers ----------------
# def parse_datetime(input_str):
#     """
#     Accept multiple date formats, e.g., 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', 'YYYY-MM-DD HH:MM:SS'
#     """
#     for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
#         try:
#             return datetime.strptime(input_str, fmt)
#         except ValueError:
#             continue
#     return None

# def build_poll_text(poll_date_str):
#     poll_votes = list(votes_collection.find({"poll_date": poll_date_str}))
#     total_attendees = sum([OPTIONS[v['choice']] for v in poll_votes])
#     lines = []
#     for v in poll_votes:
#         time_str = v['time'].strftime("%Y-%m-%d %H:%M:%S")
#         lines.append(f"{v['user']} → {v['choice']} ({v['count']}) at {time_str}")
#     lines.append(f"\nTotal going: {total_attendees}/{MAX_ATTENDEES}")
#     return f"Attending ({poll_date_str}) session\n\n" + "\n".join(lines)

# def get_poll_buttons():
#     return InlineKeyboardMarkup([[InlineKeyboardButton(opt, callback_data=opt)] for opt in OPTIONS.keys()])

# # ---------------- Handlers ----------------
# async def start_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     args = context.args
#     poll_date = datetime.now().date()  # default today
#     if args:
#         # Join arguments into a single string and parse
#         input_str = " ".join(args)
#         parsed_dt = parse_datetime(input_str)
#         if not parsed_dt:
#             await update.message.reply_text(
#                 "Invalid date/time format!\nUse YYYY-MM-DD or YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS"
#             )
#             return
#         poll_date = parsed_dt.date()

#     poll_date_str = poll_date.strftime("%Y-%m-%d")

#     # Prevent multiple polls on same date
#     if polls_collection.find_one({"poll_date": poll_date_str}):
#         await update.message.reply_text(f"A poll for {poll_date_str} already exists!")
#         return

#     # Record the poll
#     polls_collection.insert_one({"poll_date": poll_date_str, "creator": update.effective_user.username, "time": datetime.now()})

#     # Clear any old votes for this poll
#     votes_collection.delete_many({"poll_date": poll_date_str})

#     question = f"Attending ({poll_date_str}) session"
#     await update.message.reply_text(question, reply_markup=get_poll_buttons())
#     print(f"[INFO] Poll started by {update.effective_user.username} for {poll_date_str}")

# async def vote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     await query.answer()
#     choice = query.data
#     user = query.from_user.username or query.from_user.first_name

#     # Find the poll date from message text
#     poll_date_str = query.message.text.split("(")[1].split(")")[0]

#     # Check if user already voted
#     if votes_collection.find_one({"user": user, "poll_date": poll_date_str}):
#         await query.answer("You already voted! Use /withdraw to vote again.", show_alert=True)
#         return

#     # Calculate total attendees
#     poll_votes = list(votes_collection.find({"poll_date": poll_date_str}))
#     total_attendees = sum([OPTIONS[v['choice']] for v in poll_votes])
#     if total_attendees + OPTIONS[choice] > MAX_ATTENDEES:
#         await query.answer("Cannot add: total attendees limit reached!", show_alert=True)
#         return

#     # Insert vote
#     votes_collection.insert_one({
#         "user": user,
#         "choice": choice,
#         "count": OPTIONS[choice],
#         "time": datetime.now(),
#         "poll_date": poll_date_str
#     })

#     # Update the main poll (global message)
#     display_text = build_poll_text(poll_date_str)
#     await query.edit_message_text(text=display_text, reply_markup=get_poll_buttons())

#     # Send personal controls only to this user (Withdraw button)
#     withdraw_keyboard = InlineKeyboardMarkup(
#         [[InlineKeyboardButton("Withdraw", callback_data=f"withdraw_{poll_date_str}")]]
#     )
#     await query.from_user.send_message(
#         text=f"You voted **{choice}** for {poll_date_str}.",
#         reply_markup=withdraw_keyboard
#     )

#     print(f"[INFO] {user} voted {choice} for {poll_date_str}")

# async def withdraw_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.callback_query
#     user = query.from_user.username or query.from_user.first_name

#     # Parse poll_date from callback_data
#     poll_date_str = query.data.split("_")[1]

#     votes_collection.delete_many({"user": user, "poll_date": poll_date_str})

#     # Update main poll
#     display_text = build_poll_text(poll_date_str)
#     await context.bot.edit_message_text(
#         chat_id=query.message.chat_id,
#         message_id=query.message.message_id - 1,  # the main poll message
#         text=display_text,
#         reply_markup=get_poll_buttons()
#     )

#     await query.edit_message_text("You withdrew your vote.")
#     print(f"[INFO] {user} withdrew vote for {poll_date_str}")

# # ---------------- Main ----------------
# if __name__ == "__main__":
#     app = ApplicationBuilder().token(config.BOT_TOKEN).build()
#     app.add_handler(CommandHandler("poll", start_poll))
#     app.add_handler(CallbackQueryHandler(vote_handler))
#     app.add_handler(CommandHandler("withdraw", withdraw_vote))

#     print("Bot is running...")
#     try:
#         app.run_polling()
#     except KeyboardInterrupt:
#         print("\nBot stopped gracefully.")




from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from pymongo import MongoClient
from datetime import datetime
import config

# ---------------- MongoDB Setup ----------------
client = MongoClient(config.MONGO_URI)
db = client["telegram_bot"]
votes_collection = db["votes"]
polls_collection = db["polls"]

client.drop_database("telegram_bot")

MAX_ATTENDEES = 10

# Options with attendee counts
OPTIONS = {
    "Me": 1,
    "Me +1": 2,
    "Me +2": 3,
    "Me +3": 4,
    "Withdraw": 0   # special case
}

# ---------------- Helpers ----------------
def parse_datetime(input_str):
    """Accepts multiple formats for poll date input."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(input_str, fmt)
        except ValueError:
            continue
    return None

def build_poll_text(poll_date_str):
    """Builds the message text showing all votes."""
    poll_votes = list(votes_collection.find({"poll_date": poll_date_str}))
    total_attendees = sum([OPTIONS[v['choice']] for v in poll_votes])
    lines = []

    for v in poll_votes:
        time_str = v['time'].strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{v['user']} → {v['choice']} ({v['count']}) at {time_str}")

    lines.append(f"\nTotal going: {total_attendees}/{MAX_ATTENDEES}")
    return f"Attending ({poll_date_str}) session\n\n" + "\n".join(lines)

def get_poll_buttons():
    """All options visible for everyone, always."""
    buttons = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in OPTIONS.keys()]
    return InlineKeyboardMarkup(buttons)

# ---------------- Handlers ----------------
async def start_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    poll_date = datetime.now().date()  # default today

    if args:
        input_str = " ".join(args)
        parsed_dt = parse_datetime(input_str)
        if not parsed_dt:
            await update.message.reply_text(
                "Invalid date/time format!\nUse YYYY-MM-DD or YYYY-MM-DD HH:MM or YYYY-MM-DD HH:MM:SS"
            )
            return
        poll_date = parsed_dt.date()

    poll_date_str = poll_date.strftime("%Y-%m-%d")

    # Prevent multiple polls on same date
    if polls_collection.find_one({"poll_date": poll_date_str}):
        await update.message.reply_text(f"A poll for {poll_date_str} already exists!")
        return

    # Record poll in DB
    polls_collection.insert_one({
        "poll_date": poll_date_str,
        "creator": update.effective_user.username,
        "time": datetime.now()
    })

    # Clear votes for this poll
    votes_collection.delete_many({"poll_date": poll_date_str})

    question = f"Attending ({poll_date_str}) session"
    await update.message.reply_text(question, reply_markup=get_poll_buttons())
    print(f"[INFO] Poll started by {update.effective_user.username} for {poll_date_str}")

async def vote_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    user = query.from_user.username or query.from_user.first_name

    try:
        poll_date_str = query.message.text.split("(")[1].split(")")[0]
    except Exception:
        await query.answer("Error: Poll date missing.", show_alert=True)
        return

    # Handle Withdraw
    if choice == "Withdraw":
        existing_vote = votes_collection.find_one({"user": user, "poll_date": poll_date_str})
        if not existing_vote:
            await query.answer("You haven't voted yet!", show_alert=True)
            return

        votes_collection.delete_many({"user": user, "poll_date": poll_date_str})
        display_text = build_poll_text(poll_date_str)
        await query.message.edit_text(text=display_text, reply_markup=get_poll_buttons())
        await query.answer("Your vote has been withdrawn.")
        print(f"[INFO] {user} withdrew vote for {poll_date_str}")
        return

    # Handle voting
    if votes_collection.find_one({"user": user, "poll_date": poll_date_str}):
        await query.answer("You already voted! Withdraw first to change.", show_alert=True)
        return

    poll_votes = list(votes_collection.find({"poll_date": poll_date_str}))
    total_attendees = sum([OPTIONS[v['choice']] for v in poll_votes])
    if total_attendees + OPTIONS[choice] > MAX_ATTENDEES:
        await query.answer("Cannot add: total attendees limit reached!", show_alert=True)
        return

    votes_collection.insert_one({
        "user": user,
        "choice": choice,
        "count": OPTIONS[choice],
        "time": datetime.now(),
        "poll_date": poll_date_str
    })

    display_text = build_poll_text(poll_date_str)
    await query.message.edit_text(text=display_text, reply_markup=get_poll_buttons())
    await query.answer(f"You voted {choice}.")
    print(f"[INFO] {user} voted {choice} for {poll_date_str}")

# ---------------- Main ----------------
if __name__ == "__main__":
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("poll", start_poll))
    app.add_handler(CallbackQueryHandler(vote_handler))

    print("Bot is running...")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\nBot stopped gracefully.")