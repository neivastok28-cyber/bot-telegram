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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
logging.basicConfig(level=logging.INFO)

session = None


# ================= UI =================
def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton("🔍 Cek Nomor", callback_data="check")],
        [InlineKeyboardButton("👤 Profile", callback_data="profile"),
         InlineKeyboardButton("📊 Dashboard", callback_data="dashboard")],
        [InlineKeyboardButton("📜 History", callback_data="history"),
         InlineKeyboardButton("📥 Export", callback_data="export")],
        [InlineKeyboardButton("🗑 Clear", callback_data="clear")]
    ]

    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")])

    return InlineKeyboardMarkup(buttons)


def back_button(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Kembali", callback_data="back")]
    ])


# ================= HELPER =================
def format_number(text):
    number = re.sub(r"\D", "", text)
    if number.startswith("0"):
        number = "62" + number[1:]
    if number.startswith("62") and len(number) >= 10:
        return number
    return None


# ================= QUOTA =================
def get_quota(user_id):
    return int(r.get(f"quota:{user_id}") or 0) if r else 0


def set_quota(user_id, amount):
    if r:
        r.set(f"quota:{user_id}", amount)


def add_quota(user_id, amount):
    if r:
        r.incrby(f"quota:{user_id}", amount)


def use_quota(user_id):
    if not r:
        return True
    if get_quota(user_id) <= 0:
        return False
    r.decr(f"quota:{user_id}")
    return True


# ================= USAGE =================
def add_usage(user_id):
    if r:
        r.incr(f"usage:{user_id}")


def get_usage(user_id):
    return int(r.get(f"usage:{user_id}") or 0) if r else 0


# ================= API =================
async def get_gcontact(number):
    url = f"https://gcontact.id/api?token={GC_TOKEN}&nomor={number}"

    for _ in range(3):
        try:
            async with session.get(url, timeout=10) as res:
                data = await res.json()
                tags = data.get("data", {}).get("getcontact", {}).get("tags")

                if data.get("success") and tags:
                    return data
        except:
            pass

        await asyncio.sleep(2)

    return {}


# ================= TAG =================
def extract_tags(data):
    try:
        tags_raw = data.get("data", {}).get("getcontact", {}).get("tags", [])
        cleaned = []

        for t in tags_raw:
            val = t.get("value")
            if val:
                cleaned.append(val.strip().lower())

        return sorted(Counter(cleaned).items(), key=lambda x: x[1], reverse=True)
    except:
        return []


# ================= CACHE =================
def get_cache(number):
    if not r:
        return None
    data = r.get(f"cache:{number}")
    return json.loads(data) if data else None


def set_cache(number, data):
    if r:
        r.setex(f"cache:{number}", 21600, json.dumps(data))


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


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 MENU UTAMA",
        reply_markup=main_menu(update.effective_user.id)
    )


# ================= MENU =================
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = update.effective_user.id

    if q.data == "back":
        return await q.edit_message_text("🤖 MENU UTAMA", reply_markup=main_menu(user_id))

    # PROFILE
    if q.data == "profile":
        return await q.edit_message_text(
            f"👤 PROFILE\n\n🆔 {user_id}\n🎟 {get_quota(user_id)}\n📊 {get_usage(user_id)}",
            reply_markup=back_button(user_id)
        )

    # DASHBOARD
    if q.data == "dashboard":
        total_users = len(r.keys("quota:*")) if r else 0
        total_usage = sum([int(r.get(k)) for k in r.keys("usage:*")]) if r else 0

        return await q.edit_message_text(
            f"📊 DASHBOARD\n\n👥 Users: {total_users}\n📈 Total Usage: {total_usage}",
            reply_markup=back_button(user_id)
        )

    # HISTORY
    if q.data == "history":
        raw = r.lrange(f"history:{user_id}", 0, 10)
        text = "\n".join([json.loads(x)["number"] for x in raw]) or "-"
        return await q.edit_message_text(f"📜\n{text}", reply_markup=back_button(user_id))

    # EXPORT
    if q.data == "export":
        return await export_history(update, context)

    # CLEAR
    if q.data == "clear":
        r.delete(f"history:{user_id}")
        return await q.edit_message_text("🗑 Cleared", reply_markup=back_button(user_id))

    # ADMIN
    if q.data == "admin":
        return await q.edit_message_text("⚙️ /setquota /addquota", reply_markup=back_button(user_id))

    # CHECK
    if q.data == "check":
        return await q.edit_message_text("📱 Kirim nomor")


# ================= HANDLE =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not use_quota(user_id):
        return await update.message.reply_text("❌ quota habis")

    number = format_number(update.message.text)
    if not number:
        return await update.message.reply_text("❌ nomor salah")

    loading = await update.message.reply_text("🔎 mencari...")

    cached = get_cache(number)
    data = cached if cached else await get_gcontact(number)

    if not data:
        return await loading.edit_text("❌ tidak ditemukan")

    if not cached:
        set_cache(number, data)

    add_usage(user_id)

    tags = extract_tags(data)

    context.user_data.update({
        "tags": tags,
        "page": 0
    })

    await send_page(update, context, loading)


# ================= PAGINATION =================
async def send_page(update, context, msg_obj):
    tags = context.user_data["tags"]
    page = context.user_data["page"]

    per_page = 20
    start = page * per_page
    end = start + per_page

    page_tags = tags[start:end]

    text = "\n".join([f"{i}. {t} ({c})"
                      for i, (t, c) in enumerate(page_tags, start=start+1)])

    buttons = []
    if start > 0:
        buttons.append(InlineKeyboardButton("⬅️", callback_data="prev"))
    if end < len(tags):
        buttons.append(InlineKeyboardButton("➡️", callback_data="next"))

    markup = InlineKeyboardMarkup([buttons]) if buttons else None

    await msg_obj.edit_text(text or "❌ kosong", reply_markup=markup)


async def pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    context.user_data["page"] += 1 if q.data == "next" else -1
    await send_page(update, context, q.message)


# ================= EXPORT =================
async def export_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = r.lrange(f"history:{user_id}", 0, 9999)

    wb = Workbook()
    ws = wb.active

    for i, item in enumerate(raw, 1):
        obj = json.loads(item)
        ws.append([i, obj["number"], obj["name"], len(obj["tags"])])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    await context.bot.send_document(chat_id=user_id, document=bio, filename="history.xlsx")


# ================= ADMIN =================
async def addquota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    add_quota(int(context.args[0]), int(context.args[1]))
    await update.message.reply_text("ok")


async def setquota(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    set_quota(int(context.args[0]), int(context.args[1]))
    await update.message.reply_text("ok")


# ================= INIT =================
async def init(app):
    global session
    session = aiohttp.ClientSession()


# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TOKEN).post_init(init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addquota", addquota))
    app.add_handler(CommandHandler("setquota", setquota))

    app.add_handler(CallbackQueryHandler(menu, pattern="^(check|profile|dashboard|history|export|clear|admin|back)$"))
    app.add_handler(CallbackQueryHandler(pagination, pattern="^(next|prev)$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
