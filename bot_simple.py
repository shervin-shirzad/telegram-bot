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
)
from keep_alive import keep_alive  # وب‌سرور برای آنلاین ماندن

CONFIG_FILE = 'config.json'
BAD_WORDS = ["کص", "کون", "کیر"]
votes = {}          # ذخیره رأی‌ها
last_messages = {}  # ذخیره آخرین پیام کاربران برای ویرایش

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- Config ----------------
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

# ---------------- Helpers ----------------
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
                         ftype=None):
    msg_kwargs = {"chat_id": chat_id, "caption": text} if ftype else {"chat_id": chat_id, "text": text}
    if ftype == "photo":
        msg = await context.bot.send_photo(photo=file, **msg_kwargs)
    elif ftype == "video":
        msg = await context.bot.send_video(video=file, **msg_kwargs)
    elif ftype == "document":
        msg = await context.bot.send_document(document=file, **msg_kwargs)
    else:
        msg = await context.bot.send_message(**msg_kwargs)

    votes[msg.message_id] = {"like": 0, "dislike": 0, "voters": set()}

    if cfg.get("voting_enabled"):
        await msg.edit_reply_markup(reply_markup=build_vote_keyboard(msg.message_id))

    return msg.message_id  # بازگرداندن id پیام برای ویرایش

# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == 'private':
        await update.message.reply_text("پیام خودت را اینجا بفرست تا ناشناس در گروه ارسال شود.")
    else:
        await update.message.reply_text("ادمین باید /setgroup را داخل گروه بزند.")

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("این دستور باید داخل گروه زده شود.")
        return
    cfg["group_id"] = chat.id
    save_config(cfg)
    await update.message.reply_text("این گروه به‌عنوان مقصد پیام‌های ناشناس تنظیم شد ✅")

# ---------------- Voting ----------------
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

# ---------------- Settings ----------------
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("این دستور فقط در گروه قابل استفاده است.")
        return
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ فعال کردن رأی‌گیری", callback_data="voting_on")],
        [InlineKeyboardButton("❌ غیرفعال کردن رأی‌گیری", callback_data="voting_off")]
    ])
    await update.message.reply_text("تنظیمات ربات:", reply_markup=keyboard)

async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "voting_on":
        cfg["voting_enabled"] = True
        save_config(cfg)
        await query.edit_message_text("رأی‌گیری فعال شد ✅")
    elif query.data == "voting_off":
        cfg["voting_enabled"] = False
        save_config(cfg)
        await query.edit_message_text("رأی‌گیری غیرفعال شد ❌")

# ---------------- Edit Last Message ----------------
async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in last_messages:
        await update.message.reply_text("پیام قبلی برای ویرایش پیدا نشد.")
        return
    await update.message.reply_text("لطفاً متن جدید پیام خود را در پیام بعدی وارد کنید.")
    context.user_data["editing"] = True

async def handle_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    group_id = cfg.get("group_id")
    if not group_id:
        await update.message.reply_text("گروه هنوز تنظیم نشده. ادمین ابتدا /setgroup را اجرا کند.")
        return

    msg = update.message
    text = censor_text(msg.caption or msg.text or "")

    if context.user_data.get("editing"):
        # ویرایش پیام قبلی
        old_msg_id = last_messages.get(msg.from_user.id)
        if old_msg_id:
            try:
                await context.bot.edit_message_text(chat_id=group_id, message_id=old_msg_id, text=text)
                await msg.reply_text("پیام شما ویرایش شد ✅")
            except Exception as e:
                logger.exception("خطا در ویرایش پیام: %s", e)
                await msg.reply_text("خطا در ویرایش پیام.")
        context.user_data["editing"] = False
        return

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

        sent_msg_id = await send_anonymous(context, group_id, text, file=tmp_file, ftype=ftype)
        last_messages[msg.from_user.id] = sent_msg_id
        await msg.reply_text("پیامت ناشناس به گروه فرستاده شد ✅")
    except Exception as e:
        logger.exception("خطا در ارسال: %s", e)
        await msg.reply_text("خطا در ارسال پیام به گروه.")

# ---------------- Main ----------------
def main():
    TOKEN = os.environ["BOT_TOKEN"]
    keep_alive()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("settings", settings))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern="voting_"))
    app.add_handler(CommandHandler("edit", edit))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private))
    app.add_handler(CallbackQueryHandler(vote_callback, pattern="\\d+:"))

    print("Bot started ✅")
    app.run_polling()

if __name__ == "__main__":
    main()
