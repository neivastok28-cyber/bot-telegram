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
    url = f"https://wa.me/{number}"
    try:
        res = requests.get(url)
        return res.status_code == 200
    except:
        return False

def fake_getcontact(number):
    # SIMULASI DATA TAG
    return [f"User {i} ({i})" for i in range(1, 201)]

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

    # SIMPAN KE MEMORY
    context.user_data["tags"] = tags
    context.user_data["page"] = 0

    await send_page(update, context, number, wa_status)

async def send_page(update, context, number, wa_status):
    tags = context.user_data.get("tags", [])
    page = context.user_data.get("page", 0)

    per_page = 85
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    text_tags = "\n".join(
        [f"• {t.replace('(', '*(').replace(')', ')*')}" for t in page_tags]
    )

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

    await send_page(update, context, "", "")

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not r:
        return await update.message.reply_text("❌ Redis tidak aktif")

    data = r.lrange(f"history:{update.effective_user.id}", 0, 9)

    if not data:
        return await update.message.reply_text("Kosong")

    msg = "\n".join([f"• {x}" for x in data])
    await update.message.reply_text(f"📜 History:\n{msg}")

# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(pagination))
    app.add_handler(MessageHandler(filters.Regex("^/history$"), history))

    print("BOT RUNNING...")
    app.run_polling()

if __name__ == "__main__":
    main()
