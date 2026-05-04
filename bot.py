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
print("TOKEN:", os.getenv("BOT_TOKEN"))
print("REDIS:", os.getenv("REDIS_URL"))

TOKEN = os.getenv("BOT_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

logging.basicConfig(level=logging.INFO)

# ================= FORMAT NOMOR =================
def format_number(text):
    number = re.sub(r"\D", "", text)

    if number.startswith("0"):
        number = "62" + number[1:]
    elif number.startswith("62"):
        pass
    elif number.startswith("8"):
        number = "62" + number

    return number

# ================= CEK WHATSAPP =================
def cek_wa(number):
    url = f"https://wa.me/{number}"
    try:
        res = requests.get(url)
        return res.status_code == 200
    except:
        return False

# ================= FAKE GETCONTACT =================
def fake_getcontact(number):
    return [f"User {i} ({i})" for i in range(1, 201)]

# ================= HANDLER =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if not re.search(r"\d{8,15}", text):
        return await update.message.reply_text("❌ Nomor tidak valid")

    number = format_number(text)

    if not number.startswith("628"):
        return await update.message.reply_text("❌ Gunakan nomor Indonesia (628xxx)")

    # SAVE HISTORY
    if r:
        r.lpush(f"history:{update.effective_user.id}", number)

    tags = fake_getcontact(number)

    wa_status = "✅ Aktif" if cek_wa(number) else "❌ Tidak aktif"

    context.user_data["tags"] = tags
    context.user_data["page"] = 0
    context.user_data["number"] = number
    context.user_data["wa"] = wa_status

    await send_page(update, context)

# ================= PAGINATION =================
async def send_page(update, context):
    tags = context.user_data.get("tags", [])
    page = context.user_data.get("page", 0)
    number = context.user_data.get("number", "")
    wa_status = context.user_data.get("wa", "")

    per_page = 85
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    text_tags = "\n".join(
        [f"• {t.replace('(', '*(').replace(')', ')*')}" for t in page_tags]
    )

    msg = f"""📱 *{number}*
🌐 https://wa.me/{number}

WhatsApp: {wa_status}
Total Tag: *{len(tags)}*

{text_tags}
"""

    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data="prev"))
    if end < len(tags):
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next"))

    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

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

# ================= HISTORY =================
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
        print("❌ TOKEN KOSONG!")
        return

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(pagination))
    app.add_handler(MessageHandler(filters.Regex("^/history$"), history))

    print("BOT RUNNING...")
    app.run_polling()

if __name__ == "__main__":
    main()
