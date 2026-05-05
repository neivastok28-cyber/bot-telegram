import logging
import os
import re
import requests
import redis
import html

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
GC_TOKEN = os.getenv("GC_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

logging.basicConfig(level=logging.INFO)

# ================= FUNCTION =================

def format_number(text):
    number = re.sub(r"\D", "", text)

    if number.startswith("0"):
        number = "62" + number[1:]
    elif number.startswith("62"):
        pass
    elif number.startswith("8"):
        number = "62" + number
    else:
        return ""

    return number

def cek_wa(number):
    try:
        res = requests.get(f"https://wa.me/{number}", timeout=5)
        return res.status_code == 200
    except:
        return False

def get_gc(number):
    try:
        url = f"https://gcontact.id/api?token={GC_TOKEN}&nomor={number}"
        res = requests.get(url, timeout=10).json()

        print("GC RESPONSE:", res)

        if res.get("status") == True:
            return res.get("data", [])
        return []
    except Exception as e:
        print("GC ERROR:", e)
        return []

# ================= HANDLER =================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        print("MASUK:", update.message.text)

        text = update.message.text
        if not text:
            return

        number = format_number(text)

        if not number or len(number) < 10:
            return await update.message.reply_text("❌ Nomor tidak valid")

        # SAVE HISTORY
        if r:
            r.lpush(f"history:{update.effective_user.id}", number)

        tags = get_gc(number)
        wa_status = "✅ Aktif" if cek_wa(number) else "❌ Tidak aktif"

        context.user_data["tags"] = tags
        context.user_data["page"] = 0
        context.user_data["number"] = number
        context.user_data["wa"] = wa_status

        await send_page(update, context)

    except Exception as e:
        print("ERROR HANDLE:", e)
        await update.message.reply_text("❌ ERROR DI SERVER")

async def send_page(update, context):
    tags = context.user_data.get("tags", [])
    page = context.user_data.get("page", 0)
    number = context.user_data.get("number", "")
    wa_status = context.user_data.get("wa", "")

    per_page = 20
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    text_tags = "\n".join([f"• {html.escape(str(t))}" for t in page_tags])

    msg = f"""
📱 <b>{number}</b>
🌐 https://wa.me/{number}

WhatsApp: {wa_status}
Total Tag: <b>{len(tags)}</b>

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
            parse_mode="HTML",
            reply_markup=reply_markup,
        )
    else:
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
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
        return await update.message.reply_text("Kosong")

    msg = "\n".join([f"• {x}" for x in data])
    await update.message.reply_text(f"📜 History:\n{msg}")

# ================= MAIN =================

import asyncio

async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # 🔥 PENTING: pakai ALL biar pasti ke-trigger
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    app.add_handler(CallbackQueryHandler(pagination))
    app.add_handler(MessageHandler(filters.Regex("^/history$"), history))

    print("🚀 BOT RUNNING...")

    await app.initialize()
    await app.start()

    await app.bot.delete_webhook(drop_pending_updates=True)

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
