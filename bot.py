import logging
import os
import re
import json
import asyncio
import html
import math
from collections import Counter
from io import BytesIO

import aiohttp
import redis
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

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None

logging.basicConfig(level=logging.INFO)

# ================= GLOBAL SESSION =================
session = None


# ================= HELPER =================

def format_number(text):
    number = re.sub(r"\D", "", text)

    if number.startswith("0"):
        number = "62" + number[1:]

    if number.startswith("62") and len(number) >= 10:
        return number

    return None


# 🔥 API REQUEST + VALIDASI TAG
async def get_gcontact(number):
    global session

    url = f"https://gcontact.id/api?token={GC_TOKEN}&nomor={number}"

    for i in range(3):
        try:
            async with session.get(url, timeout=10) as res:
                text = await res.text()

                print("RAW:", text[:200])  # debug

                try:
                    data = json.loads(text)
                except:
                    print("❌ BUKAN JSON")
                    return {}

                tags = data.get("data", {}).get("getcontact", {}).get("tags")

                # ✅ VALIDASI FINAL
                if data.get("success") and tags:
                    return data

                print("❌ INVALID / NO TAGS:", data)

        except Exception as e:
            print("RETRY ERROR:", e)

        await asyncio.sleep(2)

    return {}


# 🔥 NORMALISASI TAG
def extract_tags(data):
    try:
        tags_raw = data.get("data", {}).get("getcontact", {}).get("tags", [])

        cleaned = []

        for t in tags_raw:
            val = t.get("value")
            if not val:
                continue

            val = val.strip().lower()
            val = re.sub(r"\s+", " ", val)

            cleaned.append(val)

        counter = Counter(cleaned)

        return sorted(counter.items(), key=lambda x: x[1], reverse=True)

    except:
        return []


# ================= CACHE =================

def get_cache(number):
    if not r:
        return None

    data = r.get(f"cache:{number}")
    return json.loads(data) if data else None


def set_cache(number, data):
    if not r:
        return
    r.setex(f"cache:{number}", 21600, json.dumps(data))  # 6 jam


# ================= HISTORY =================

def remove_duplicate_history(user_id, number):
    raw = r.lrange(f"history:{user_id}", 0, -1)

    for item in raw:
        try:
            obj = json.loads(item)
            if obj.get("number") == number:
                r.lrem(f"history:{user_id}", 0, item)
        except:
            pass


# ================= HANDLER =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot aktif!\n\n"
        "📱 Kirim nomor untuk cek\n"
        "📜 /history\n"
        "📥 /export\n"
        "🗑 /clear"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    number = format_number(text)
    if not number:
        return await update.message.reply_text("❌ Nomor tidak valid")

    user_id = update.effective_user.id

    loading = await update.message.reply_text("🔎 Sedang mencari...")

    # CACHE
    cached = get_cache(number)

    if cached:
        data = cached
        print("⚡ CACHE HIT")
    else:
        await asyncio.sleep(1)

        data = await get_gcontact(number)

        if not data:
            return await loading.edit_text(
                "❌ Data tidak ditemukan / quota habis\n\n"
                "💡 Coba nomor lain atau tunggu beberapa saat"
            )

        set_cache(number, data)

    # PROSES
    tags = extract_tags(data) if data else []
    name = tags[0][0] if tags else "-"

    # HISTORY
    if r:
        remove_duplicate_history(user_id, number)

        history_data = {
            "number": number,
            "name": name,
            "tags": tags
        }

        r.lpush(f"history:{user_id}", json.dumps(history_data))

    context.user_data.update({
        "tags": tags,
        "page": 0,
        "number": number,
        "name": name
    })

    await send_page(update, context, edit_msg=loading)


# ================= PAGINATION =================

async def send_page(update, context, edit_msg=None):
    tags = context.user_data.get("tags", [])
    page = context.user_data.get("page", 0)
    number = context.user_data.get("number", "")
    name = context.user_data.get("name", "-")

    per_page = 85
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    text_tags = "\n".join(
        [
            f"{i}. {t.title()} >> <b>{c} Tag</b>"
            for i, (t, c) in enumerate(page_tags, start=start+1)
        ]
    ) if page_tags else "❌ Tidak ada data"

    total_page = math.ceil(len(tags) / per_page) if tags else 1

    msg = f"""📱 {number}
💬 https://wa.me/{number}

👤 {html.escape(name.title())}
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
        await edit_msg.edit_text(msg, reply_markup=reply_markup, parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="HTML")


async def pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "next":
        context.user_data["page"] += 1
    else:
        context.user_data["page"] -= 1

    await send_page(update, context)


# ================= HISTORY =================

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = r.lrange(f"history:{user_id}", 0, 50)

    data = []
    for item in raw:
        try:
            obj = json.loads(item)
            data.append(obj["number"])
        except:
            pass

    text = "\n".join([f"{i+1}. {x}" for i, x in enumerate(data)])

    await update.message.reply_text(f"📜 History:\n\n{text}")


# ================= CLEAR =================

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if r:
        r.delete(f"history:{user_id}")
    await update.message.reply_text("🗑 History berhasil dihapus")


# ================= EXPORT =================

async def export_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = r.lrange(f"history:{user_id}", 0, 9999)

    wb = Workbook()
    ws = wb.active

    ws.append(["No", "Nomor", "Nama", "Total Tag"])

    for i, item in enumerate(raw, start=1):
        try:
            obj = json.loads(item)
            ws.append([i, obj["number"], obj["name"], len(obj["tags"])])
        except:
            pass

    file_stream = BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    await update.message.reply_document(file_stream, filename="history.xlsx")


# ================= INIT =================

async def init(app):
    global session
    session = aiohttp.ClientSession()
    await app.bot.delete_webhook(drop_pending_updates=True)


# ================= MAIN =================

def main():
    app = ApplicationBuilder().token(TOKEN).post_init(init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("export", export_history))
    app.add_handler(CommandHandler("clear", clear_history))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(pagination, pattern="^(next|prev)$"))

    print("🚀 BOT RUNNING...")
    app.run_polling()


if __name__ == "__main__":
    main()
