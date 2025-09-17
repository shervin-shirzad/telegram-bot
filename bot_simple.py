import os
import json
import logging
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

from keep_alive import keep_alive  # وب‌سرور برای آنلاین ماندن

CONFIG_FILE = 'config.json'
BAD_WORDS = ["کص", "کون", "کیر"]
votes = {}  # ذخیره رأی‌ها
last_messages = {}  # ذخیره آخرین پیام هر کاربر برای ویرایش

EDIT_WAITING = 1  # مرحله انتظار برای متن جدید ویرایش

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Config ----------
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
    else:
        cfg = {"group_id": None, "voting_enabled": False}
        save_config(cfg)
    if "voting_enabled" not in cfg:
        cfg["voting_enabled"] = False
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
    if not cfg.get("voting_enabled", False):
        return None
    like = votes.get(message_id, {}).get("like", 0)
    dislike = votes.get(message_id, {}).get("dislike", 0)
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"👍 {like}", callback_data=f"{message_id}:like"),
        InlineKeyboardButton(f"👎 {dislike}", callback_data=f"{message_id}:dislike")
    ]])

async def send_anonymous(context: ContextTypes.DEFAULT_TYPE, chat_id, text, file=None, ftype=None):
    msg_kwargs = {"chat_id": chat_id, "caption": text} if ftype else {"chat_id": chat_id, "text": text}
    if ftype == "photo":
        msg = await context.bot.send_photo(photo=file, **msg_kwargs)
    elif ftype == "video":
        msg = await context.bot.send_video(video=file, **msg_kwargs)
    elif ftype == "document":
        msg = await context.bot.send_document(document=file, **msg_kwargs)
    else:
        msg = await context.bot.send_message(**msg_kwargs)

    # ذخیره رأی‌ها و دکمه رأی‌گیری
    if cfg.get("voting_enabled", False):
        votes[msg.message_id] = {"like": 0, "dislike": 0, "voters": set()}
        await msg.edit_reply_markup(reply_markup=build_vote_keyboard(msg.message_id))

    return msg

# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        await update.message.reply_text(
            "سلام 👋\nپیام خودت را اینجا بفرست تا ناشناس در گروه ارسال شود.\nبرای مشاهده دستورها /help را بزنید."
        )
    else:
        await update.message.reply_text(
            "ادمین باید /setgroup را داخل گروه بزند."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "دستورهای ربات:\n"
        "/start - شروع کار با ربات\n"
        "/setgroup - تعیین گروه برای پیام‌های ناشناس\n"
        "/edit - ویرایش آخرین پیام ارسال شده\n"
        "/settings - تنظیمات ربات (فعال/غیرفعال کردن رأی‌گیری)\n"
        "/help - نمایش این راهنما"
    )
    await update.message.reply_text(help_text)

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("این دستور باید داخل گروه زده شود.")
        return
    cfg["group_id"] = chat.id
    save_config(cfg)
    await update.message.reply_text("این گروه به‌عنوان مقصد پیام‌های ناشناس تنظیم شد ✅")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != 'private':
        await update.message.reply_text("تنظیمات فقط در چت خصوصی قابل تغییر است.")
        return
    current = cfg.get("voting_enabled", False)
    cfg["voting_enabled"] = not current
    save_config(cfg)
    status = "فعال" if cfg["voting_enabled"] else "غیرفعال"
    await update.message.reply_text(f"قابلیت رأی‌گیری اکنون {status} شد.")

async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    group_id = cfg.get("group_id")
    if not group_id:
        await update.message.reply_text("گروه هنوز تنظیم نشده. ادمین ابتدا /setgroup را اجرا کند.")
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

        sent_msg = await send_anonymous(context, group_id, text, file=tmp_file, ftype=ftype)
        last_messages[msg.from_user.id] = sent_msg.message_id  # ذخیره آخرین پیام کاربر
        await update.message.reply_text("پیامت ناشناس به گروه فرستاده شد ✅")
    except Exception as e:
        logger.exception("خطا در ارسال: %s", e)
        await update.message.reply_text("خطا در ارسال پیام به گروه.")

# ---------- ویرایش پیام ----------
async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in last_messages:
        await update.message.reply_text("شما هنوز هیچ پیامی ارسال نکرده‌اید.")
        return ConversationHandler.END
    await update.message.reply_text("لطفاً متن جدیدی که می‌خواهید جایگزین کنید را ارسال کنید:")
    return EDIT_WAITING

async def receive_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in last_messages:
        await update.message.reply_text("هیچ پیام قابل ویرایشی پیدا نشد.")
        return ConversationHandler.END

    new_text = censor_text(update.message.text)
    group_id = cfg.get("group_id")
    message_id = last_messages[user_id]

    try:
        # ویرایش پیام در گروه
        await context.bot.edit_message_text(chat_id=group_id, message_id=message_id, text=new_text)
        await update.message.reply_text("پیام شما با موفقیت ویرایش شد ✅")
    except Exception as e:
        logger.exception("خطا در ویرایش پیام: %s", e)
        await update.message.reply_text("خطا در ویرایش پیام.")

    return ConversationHandler.END

# ---------- رأی‌گیری ----------
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
        await query.message.edit_reply_markup(reply_markup=build_vote_keyboard(message_id))
    except Exception as e:
        logger.exception("خطا در رأی‌گیری: %s", e)

# ---------- Main ----------
def main():
    TOKEN = os.environ["BOT_TOKEN"]
    keep_alive()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settings", settings_command))

    # هندلر ویرایش با ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_command)],
        states={EDIT_WAITING: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit)]},
        fallbacks=[],
    )
    app.add_handler(conv_handler)

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private))
    app.add_handler(CallbackQueryHandler(vote_callback))

    print("Bot started ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
