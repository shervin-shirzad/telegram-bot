import os
import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from keep_alive import keep_alive  # وب‌سرور برای آنلاین ماندن

CONFIG_FILE = 'config.json'
BAD_WORDS = ["کص", "کون", "کیر"]
votes = {}  # ذخیره رأی‌ها
last_messages = {}  # ذخیره آخرین پیام هر کاربر: user_id -> message_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Config ----------
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    cfg = {"group_id": None}
    save_config(cfg)
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

cfg = load_config()

# ---------- Helpers ----------
def censor_text(text):
    for word in BAD_WORDS:
        text = text.replace(word, "***")
    return text

def build_vote_keyboard(message_id):
    like = votes.get(message_id, {}).get("like", 0)
    dislike = votes.get(message_id, {}).get("dislike", 0)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"👍 {like}", callback_data=f"{message_id}:like"),
        InlineKeyboardButton(f"👎 {dislike}", callback_data=f"{message_id}:dislike")
    ]])

async def send_anonymous(context: ContextTypes.DEFAULT_TYPE,
                         chat_id,
                         text,
                         file=None,
                         ftype=None,
                         user_id=None):
    msg_kwargs = {
        "chat_id": chat_id,
        "caption": text
    } if ftype else {
        "chat_id": chat_id,
        "text": text
    }

    if ftype == "photo":
        msg = await context.bot.send_photo(photo=file, **msg_kwargs)
    elif ftype == "video":
        msg = await context.bot.send_video(video=file, **msg_kwargs)
    elif ftype == "document":
        msg = await context.bot.send_document(document=file, **msg_kwargs)
    else:
        msg = await context.bot.send_message(**msg_kwargs)

    # ذخیره آراء برای پیام جدید
    votes[msg.message_id] = {"like": 0, "dislike": 0, "voters": set()}

    # اضافه کردن دکمه رأی
    await msg.edit_reply_markup(reply_markup=build_vote_keyboard(msg.message_id))

    # ذخیره آخرین پیام کاربر
    if user_id:
        last_messages[user_id] = msg.message_id

    return msg

# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        await update.message.reply_text(
            "سلام 👋\nپیام خودت را اینجا بفرست تا ناشناس در گروه ارسال شود.\n\n"
            "دستورها:\n"
            "/edit متن جدید - ویرایش آخرین پیام\n"
            "/help - نمایش دستورها"
        )
    else:
        await update.message.reply_text(
            "ادمین باید /setgroup را داخل گروه بزند.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "دستورهای ربات:\n"
        "/start - شروع ربات و راهنمایی\n"
        "/setgroup - تعیین گروه برای پیام‌های ناشناس (فقط در گروه)\n"
        "/edit متن جدید - ویرایش آخرین پیام ناشناس شما\n"
        "/help - نمایش این راهنما"
    )

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("این دستور باید داخل گروه زده شود.")
        return
    cfg["group_id"] = chat.id
    save_config(cfg)
    await update.message.reply_text("این گروه به‌عنوان مقصد پیام‌های ناشناس تنظیم شد ✅")

async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    group_id = cfg.get("group_id")
    if not group_id:
        await update.message.reply_text(
            "گروه هنوز تنظیم نشده. ادمین ابتدا /setgroup را اجرا کند.")
        return

    msg = update.message
    text = censor_text(msg.caption or msg.text or "")

    tmp_file = None
    ftype = None
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            tmp_file = file_obj.file_id
            ftype = "photo"
        elif msg.video:
            file_obj = await msg.video.get_file()
            tmp_file = file_obj.file_id
            ftype = "video"
        elif msg.document:
            file_obj = await msg.document.get_file()
            tmp_file = file_obj.file_id
            ftype = "document"

        await send_anonymous(context,
                             group_id,
                             text,
                             file=tmp_file,
                             ftype=ftype,
                             user_id=msg.from_user.id)
        await update.message.reply_text("پیامت ناشناس به گروه فرستاده شد ✅")
    except Exception as e:
        logger.exception("خطا در ارسال: %s", e)
        await update.message.reply_text("خطا در ارسال پیام به گروه.")

async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        message_id_str, vote_type = query.data.split(":")
        message_id = int(message_id_str)
        user_id = query.from_user.id

        if user_id in votes[message_id]["voters"]:
            await query.answer("شما قبلاً رأی داده‌اید!")
            return

        votes[message_id]["voters"].add(user_id)
        votes[message_id][vote_type] += 1

        await query.message.edit_reply_markup(
            reply_markup=build_vote_keyboard(message_id))
    except Exception as e:
        logger.exception("خطا در رأی‌گیری: %s", e)

# ---------- Edit Last Message ----------
async def edit_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in last_messages:
        await update.message.reply_text("پیام قبلی پیدا نشد.")
        return

    msg_id = last_messages[user_id]
    new_text = " ".join(context.args)
    if not new_text:
        await update.message.reply_text("لطفاً متن جدید را بعد از /edit بنویسید.")
        return

    group_id = cfg.get("group_id")
    try:
        # ویرایش متن پیام بدون پاک شدن دکمه‌ها و آرا
        await context.bot.edit_message_text(
            chat_id=group_id,
            message_id=msg_id,
            text=new_text,
            reply_markup=build_vote_keyboard(msg_id)
        )
        await update.message.reply_text("پیام آخر شما ویرایش شد ✅")
    except Exception as e:
        logger.exception("خطا در ویرایش پیام: %s", e)
        await update.message.reply_text("خطا در ویرایش پیام.")

# ---------- Main ----------
def main():
    TOKEN = os.environ["BOT_TOKEN"]
    keep_alive()
    app = ApplicationBuilder().token(TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("edit", edit_last))

    # Message and callback handlers
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private))
    app.add_handler(CallbackQueryHandler(vote_callback))

    print("Bot started ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
