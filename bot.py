import os
import re
import logging
import requests
import redis

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
GC_TOKEN = os.getenv("GC_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

print("BOT TOKEN:", TOKEN)
print("GC TOKEN:", GC_TOKEN)
print("REDIS:", REDIS_URL)

logging.basicConfig(level=logging.INFO)

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

# ================= FUNCTION =================

def format_number(text):
    number = re.sub(r"\D", "", text)

    if number.startswith("62"):
        return number
    elif number.startswith("0"):
        return "62" + number[1:]
    elif number.startswith("8"):
        return "62" + number
    else:
        return number

def valid_number(text):
    digits = re.sub(r"\D", "", text)
    return 8 <= len(digits) <= 15

def get_gcontact(number):
    try:
        url = f"https://gcontact.id/api?token={GC_TOKEN}&nomor={number}"
        res = requests.get(url, timeout=10)
        data = res.json()

        print("GC RESPONSE:", data)

        if isinstance(data, dict) and "data" in data:
            return data["data"]

        return []
    except Exception as e:
        print("GC ERROR:", e)
        return []

# ================= HANDLER =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("DAPAT /start")
    await update.message.reply_text("✅ Bot aktif! Kirim nomor untuk cek")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    print("DAPAT PESAN:", text)

    if not text:
        return

    if not valid_number(text):
        return await update.message.reply_text("❌ Nomor tidak valid")

    number = format_number(text)

    # simpan history (optional)
    if r:
        r.lpush(f"history:{update.effective_user.id}", number)

    tags = get_gcontact(number)

    if not tags:
        return await update.message.reply_text("❌ Data tidak ditemukan")

    hasil = "\n".join([f"• {str(t)}" for t in tags[:20]])

    msg = f"""📱 {number}
Total Tag: {len(tags)}

{hasil}
"""

    await update.message.reply_text(msg)

# ================= MAIN =================

def main():
    if not TOKEN:
        print("❌ BOT_TOKEN belum diset")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 BOT RUNNING...")

    app.run_polling()

if __name__ == "__main__":
    main()
