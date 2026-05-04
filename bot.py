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

# ================= DEBUG =================
print("BOT TOKEN:", os.getenv("BOT_TOKEN"))
print("GC TOKEN:", os.getenv("GC_TOKEN"))
print("REDIS:", os.getenv("REDIS_URL"))

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
    else:
        number = "62" + number

    return number


def cek_wa(number):
    try:
        res = requests.get(f"https://wa.me/{number}", timeout=5)
        return res.status_code == 200
    except:
        return False


def getcontact_api(number):
    try:
        if not GC_TOKEN:
            return ["❌ GC TOKEN belum diset"]

        # CACHE
        if r:
            cache = r.get(f"gc:{number}")
            if cache:
                return eval(cache)

        url = "https://gcontact.id/api"
        params = {
            "token": GC_TOKEN,
            "nomor": number
        }

        res = requests.get(url, params=params, timeout=10)
        data = res.json()

        print("API RESPONSE:", data)

        if "data" not in data:
            return ["Tidak ada data"]

        result = []

        for item in data["data"]:
            if isinstance(item, dict):
                result.append(item.get("name", str(item)))
            else:
                result.append(str(item))

        # SAVE CACHE
        if r:
            r.setex(f"gc:{number}", 3600, str(result))

        return result if result else ["Tidak ada tag"]

    except Exception as e:
        print("API ERROR:", e)
        return ["Error API"]


# ================= HANDLER =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot aktif!\n\nKirim nomor untuk cek:\nContoh: 08123456789"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    number = format_number(text)

    # VALIDASI FIX
    if len(number) < 10:
        return await update.message.reply_text("❌ Nomor tidak valid")

    # HISTORY
    if r:
        r.lpush(f"history:{update.effective_user.id}", number)

    tags = getcontact_api(number)
    wa_status = "✅ Aktif" if cek_wa(number) else "❌ Tidak aktif"

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

    formatted_tags = []
    for t in page_tags:
        clean = re.sub(r"\((.*?)\)", r"*(\1)*", t)
        formatted_tags.append(f"• {clean}")

    text_tags = "\n".join(formatted_tags)

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
        return await update.message.reply_text("Kosong")

    msg = "\n".join([f"• {x}" for x in data])
    await update.message.reply_text(f"📜 History:\n{msg}")


# ================= MAIN =================

def main():
    if not TOKEN:
        print("❌ BOT TOKEN BELUM DISET")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(pagination))
    app.add_handler(MessageHandler(filters.Regex("^/history$"), history))

    print("🚀 BOT RUNNING...")
    app.run_polling()


if __name__ == "__main__":
    main()
