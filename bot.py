import logging
import os
import re
import requests
import redis
import json
import asyncio
from collections import Counter
from io import BytesIO
from openpyxl import Workbook

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
GC_TOKEN = os.getenv("API_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

print("BOT TOKEN:", TOKEN)
print("GC TOKEN:", GC_TOKEN)
print("REDIS:", REDIS_URL)

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

logging.basicConfig(level=logging.INFO)

# ================= HELPER =================

def format_number(text):
    number = re.sub(r"\D", "", text)

    if number.startswith("0"):
        number = "62" + number[1:]

    if number.startswith("62") and len(number) >= 10:
        return number

    return None


def get_gcontact(number):
    try:
        url = f"https://gcontact.id/api?token={GC_TOKEN}&nomor={number}"
        return requests.get(url, timeout=10).json()
    except:
        return {}


def extract_tags(data):
    try:
        tags_raw = data.get("data", {}).get("getcontact", {}).get("tags", [])
        tags = [t.get("value") for t in tags_raw if t.get("value")]

        counter = Counter(tags)
        return sorted(counter.items(), key=lambda x: x[1], reverse=True)
    except:
        return []

# ================= CACHE =================

def get_cache(number):
    if not r:
        return None

    data = r.get(f"cache:{number}")
    if data:
        try:
            return json.loads(data)
        except:
            return None
    return None


def set_cache(number, data):
    if not r:
        return
    r.setex(f"cache:{number}", 86400, json.dumps(data))  # 1 hari

# ================= HISTORY =================

def check_history(user_id, number):
    if not r:
        return False

    raw = r.lrange(f"history:{user_id}", 0, 999)
    for item in raw:
        try:
            obj = json.loads(item)
            if obj.get("number") == number:
                return True
        except:
            if item == number:
                return True
    return False

# ================= HANDLER =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot aktif!\n\n"
        "📱 Kirim nomor untuk cek\n"
        "📜 /history lihat riwayat\n"
        "📥 /export export excel"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    print("DAPAT PESAN:", text)

    number = format_number(text)
    if not number:
        return await update.message.reply_text("❌ Nomor tidak valid")

    user_id = update.effective_user.id
    pernah = check_history(user_id, number)

    # 🔎 LOADING MESSAGE
    loading = await update.message.reply_text("🔎 Sedang mencari data...")

    # ================= CACHE =================
    cached = get_cache(number)

    if cached:
        print("AMBIL DARI CACHE")
        data = cached
    else:
        print("HIT API")
        await asyncio.sleep(1)  # anti spam API
        data = get_gcontact(number)

        if not data or not data.get("success"):
            return await loading.edit_text("❌ API error / limit")

        set_cache(number, data)

    # ================= PROSES =================
    tags = extract_tags(data)

    wa = data.get("data", {}).get("whatsapp", {}).get("exist", False)
    wa_status = "✅ Aktif" if wa else "❌ Tidak aktif"

    name = tags[0][0] if tags else "-"

    # ================= SAVE HISTORY =================
    if r:
        history_data = {
            "number": number,
            "name": name,
            "tags": tags
        }
        r.lpush(f"history:{user_id}", json.dumps(history_data))

    # ================= SAVE CONTEXT =================
    context.user_data["tags"] = tags
    context.user_data["page"] = 0
    context.user_data["number"] = number
    context.user_data["wa"] = wa_status
    context.user_data["name"] = name
    context.user_data["pernah"] = pernah

    await send_page(update, context, edit_msg=loading)

# ================= PAGINATION TAG =================

async def send_page(update, context, edit_msg=None):
    tags = context.user_data.get("tags", [])
    page = context.user_data.get("page", 0)
    number = context.user_data.get("number", "")
    wa = context.user_data.get("wa", "")
    name = context.user_data.get("name", "-")
    pernah = context.user_data.get("pernah", False)

    per_page = 85
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    if not page_tags:
        text_tags = "❌ Tidak ada data"
    else:
        text_tags = "\n".join(
            [f"{i}. {t} >> {c} Tag" for i, (t, c) in enumerate(page_tags, start=start+1)]
        )

    total_page = (len(tags) // per_page) + 1
    history_text = "🕘 Pernah dicari sebelumnya" if pernah else ""

    msg = f"""📱 {number}
💬 https://wa.me/{number}
{history_text}

👤 {name}
📊 {len(tags)} tag
📄 Page {page+1}/{total_page}

{text_tags}
"""

    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data="prev"))
    if end < len(tags):
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data="next"))

    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

    if edit_msg:
        await edit_msg.edit_text(msg, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)

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
    context.user_data["history_page"] = 0
    await send_history(update, context)

async def send_history(update, context):
    user_id = update.effective_user.id
    raw = r.lrange(f"history:{user_id}", 0, 999)

    data = []
    for item in raw:
        try:
            obj = json.loads(item)
            data.append(obj["number"])
        except:
            data.append(item)

    page = context.user_data.get("history_page", 0)
    per_page = 20
    start = page * per_page
    end = start + per_page

    page_data = data[start:end]
    text = "\n".join([f"{i+1}. {x}" for i, x in enumerate(page_data, start=start)])

    msg = f"📜 History Page {page+1}\n\n{text}"

    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data="h_prev"))
    if end < len(data):
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data="h_next"))

    reply_markup = InlineKeyboardMarkup([buttons]) if buttons else None

    if update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)

async def history_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "h_next":
        context.user_data["history_page"] += 1
    elif query.data == "h_prev":
        context.user_data["history_page"] -= 1

    await send_history(update, context)

# ================= EXPORT =================

async def export_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = r.lrange(f"history:{user_id}", 0, 9999)

    wb = Workbook()
    ws = wb.active

    ws.append(["No", "Nomor", "Nama", "Total Tag", "Tags"])

    for i, item in enumerate(raw, start=1):
        try:
            obj = json.loads(item)
            number = obj["number"]
            name = obj["name"]
            tags = obj["tags"]

            total = len(tags)
            tag_text = ", ".join([f"{t} ({c})" for t, c in tags[:50]])
        except:
            number = item
            name = "-"
            total = 0
            tag_text = "-"

        ws.append([i, number, name, total, tag_text])

    file_stream = BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    await update.message.reply_document(
        document=file_stream,
        filename="history.xlsx"
    )

# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("export", export_history))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(pagination, pattern="^(next|prev)$"))
    app.add_handler(CallbackQueryHandler(history_pagination, pattern="^(h_next|h_prev)$"))

    print("🚀 BOT RUNNING...")
    app.run_polling()

if __name__ == "__main__":
    main()
