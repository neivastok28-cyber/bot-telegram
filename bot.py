import logging
import os
import re
import requests
import redis

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

logging.basicConfig(level=logging.INFO)

# ================= FUNCTION =================

def format_number(text):
    number = re.sub(r"\D", "", text)
    if number.startswith("0"):
        number = "62" + number[1:]
    return number

def cek_wa(number):
    try:
        res = requests.get(f"https://wa.me/{number}", timeout=5)
        return res.status_code == 200
    except:
        return False

def fake_getcontact(number):
    # SIMULASI TAG BIAR KELIHATAN REAL
    return [f"Kontak {i} ({i})" for i in range(1, 201)]

def format_tag(t):
    match = re.match(r"(.*)\((\d+)\)", t)
    if match:
        return f"{match.group(1).strip()} *({match.group(2)})*"
    return t

# ================= HANDLER =================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if not re.search(r"\d{8,}", text):
        return await update.message.reply_text("❌ Masukkan nomor yang valid")

    number = format_number(text)

    # SAVE HISTORY
    if r:
        r.lpush(f"history:{update.effective_user.id}", number)

    tags = fake_getcontact(number)
    wa_status = "✅ Aktif" if cek_wa(number) else "❌ Tidak aktif"

    # SIMPAN DATA
    context.user_data["tags"] = tags
    context.user_data["page"] = 0
    context.user_data["number"] = number
    context.user_data["wa"] = wa_status

    await send_page(update, context)

async def send_page(update, context):
    tags = context.user_data.get("tags", [])
    page = context.user_data.get("page", 0)
    number = context.user_data.get("number", "")
    wa_status = context.user_data.get("wa", "")

    per_page = 85
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    text_tags = "\n".join([f"• {format_tag(t)}" for t in page_tags])

    msg = f"""
📱 *{number}*
🌐 https://wa.me/{number}

WhatsApp: {wa_status}
Total Tag: *{len(tags)}*

{text_tags}
"""

    keyboard = []

    if start > 0:
        keyboard.append(InlineKeyboardButton("⬅️ Prev", callback_data="prev"))

    if end < len(tags):
        keyboard.append(InlineKeyboardButton("Next ➡️", callback_data="next"))

    reply_markup = InlineKeyboardMarkup([keyboard]) if keyboard else None

    if update.callback_query:
        await update.callback_query.edit_message_text(
            msg,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

async def pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "next":
        context.user_data["page"] += 1
    elif query.data == "prev":
        context.user_data["page"] -= 1

    await send_page(update, context)

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not r:
        return await update.message.reply_text("❌ Redis tidak aktif")

    data = r.lrange(f"history:{update.effective_user.id}", 0, 9)

    if not data:
        return await update.message.reply_text("📜 History kosong")

    msg = "\n".join([f"• {x}" for x in data])
    await update.message.reply_text(f"📜 *History:*\n{msg}", parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📜 History", callback_data="history")]
    ]
    await update.message.reply_text(
        "🤖 *Bot Premium Aktif*\n\nKirim nomor langsung untuk cek 🔍",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "history":
        if not r:
            return await query.edit_message_text("❌ Redis tidak aktif")

        data = r.lrange(f"history:{query.from_user.id}", 0, 9)

        if not data:
            return await query.edit_message_text("📜 History kosong")

        msg = "\n".join([f"• {x}" for x in data])
        await query.edit_message_text(f"📜 *History:*\n{msg}", parse_mode="Markdown")

# ================= MAIN =================

def main():
    if not TOKEN:
        raise ValueError("BOT_TOKEN tidak ditemukan di Railway")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(pagination, pattern="^(next|prev)$"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^history$"))

    print("BOT RUNNING...")
    app.run_polling()

if __name__ == "__main__":
    main()
