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
        [
            InlineKeyboardButton("👤 Profile", callback_data="profile"),
            InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
        ],
        [
            InlineKeyboardButton("🎟 Quota", callback_data="quota"),
        ],
        [
            InlineKeyboardButton("📜 History", callback_data="history"),
            InlineKeyboardButton("📥 Export", callback_data="export"),
        ],
        [InlineKeyboardButton("🗑 Clear", callback_data="clear")],
    ]

    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")])

    return InlineKeyboardMarkup(buttons)


def back_button():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data="back")]])


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


# ================= CACHE =================
def get_cache(number):
    if not r:
        return None
    data = r.get(f"cache:{number}")
    return json.loads(data) if data else None


def set_cache(number, data):
    if r:
        r.setex(f"cache:{number}", 21600, json.dumps(data))


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
def normalize_tag(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"(.)\1+", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()

    words = text.split()
    clean = []

    for w in words:
        if w.startswith("bg"):
            clean.append("bg")
        elif w.startswith("bang"):
            clean.append("bang")
        else:
            clean.append(w)

    return " ".join(clean)


def extract_tags(data):
    tags_raw = data.get("data", {}).get("getcontact", {}).get("tags", [])
    cleaned = []

    for t in tags_raw:
        val = t.get("value")
        if val:
            cleaned.append(normalize_tag(val))

    return sorted(Counter(cleaned).items(), key=lambda x: x[1], reverse=True)


# ================= SIMILAR =================
def similarity_word(a, b):
    if a == b:
        return True

    if abs(len(a) - len(b)) <= 2:
        diff = sum(1 for x, y in zip(a, b) if x != y)
        if diff <= 2:
            return True

    if a in b or b in a:
        return True

    return False


def is_similar_name(a, b):
    a_words = a.split()
    b_words = b.split()

    if not a_words or not b_words:
        return False

    if a_words[0] in ["bg", "bang"] and b_words[0] in ["bg", "bang"]:
        if a_words[0] != b_words[0]:
            return False

    same = 0

    for aw in a_words:
        for bw in b_words:
            if similarity_word(aw, bw):
                same += 1
                break

    return same >= max(1, int(min(len(a_words), len(b_words)) * 0.7))


def merge_similar_tags(tags):
    groups = []

    for tag, count in tags:
        found = False

        for g in groups:
            if is_similar_name(tag, g["key"]):
                g["count"] += count
                found = True
                break

        if not found:
            groups.append({"key": tag, "count": count})

    return sorted([(g["key"], g["count"]) for g in groups], key=lambda x: x[1], reverse=True)


# ================= FORMAT =================
def format_display(tag):
    words = tag.split()

    if words[0] == "bg":
        return "Bg " + " ".join(w.title() for w in words[1:])
    if words[0] == "bang":
        return "Bang " + " ".join(w.title() for w in words[1:])

    return " ".join(w.title() for w in words)


# ================= ANALYZE =================
def analyze_tags(tags):
    if not tags:
        return "-", "-", "-"

    dominant = tags[0][0]
    dominant_count = tags[0][1]

    alias = []
    for t, _ in tags[1:10]:
        if len(t.split()) == 1:
            alias.append(t)

    alias = " / ".join(set(alias)) if alias else "-"

    lokasi_list = ["jakarta", "bengkulu", "bandung", "surabaya", "medan"]
    lokasi = "-"

    for t, _ in tags:
        for loc in lokasi_list:
            if loc in t:
                lokasi = loc.title()
                break

    return f"{format_display(dominant)} ({dominant_count})", alias.title(), lokasi


# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 MENU UTAMA", reply_markup=main_menu(update.effective_user.id))


# ================= MENU =================
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id

    if q.data == "quota":
        return await q.edit_message_text(
            f"🎟 Sisa Quota: {get_quota(user_id)}",
            reply_markup=back_button()
        )

    if q.data == "back":
        return await q.edit_message_text("🤖 MENU UTAMA", reply_markup=main_menu(user_id))

    if q.data == "check":
        return await q.edit_message_text("📱 Kirim nomor")

    if q.data == "profile":
        return await q.edit_message_text(
            f"👤 PROFILE\n\nID: {user_id}\n🎟 {get_quota(user_id)}\n📊 {get_usage(user_id)}",
            reply_markup=back_button()
        )

    if q.data == "dashboard":
        total_users = len(r.keys("quota:*")) if r else 0
        return await q.edit_message_text(
            f"📊 DASHBOARD\nUsers: {total_users}",
            reply_markup=back_button()
        )

    if q.data == "admin":
        return await q.edit_message_text(
            "⚙️ ADMIN PANEL\n/addquota <id> <jumlah>\n/setquota <id> <jumlah>",
            reply_markup=back_button()
        )


# ================= INIT =================
async def init(app):
    global session
    session = aiohttp.ClientSession()


# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TOKEN).post_init(init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setquota", set_quota))
    app.add_handler(CommandHandler("addquota", add_quota))

    app.add_handler(CallbackQueryHandler(menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None))

    print("🚀 BOT FINAL FIX MENU QUOTA")
    app.run_polling()


if __name__ == "__main__":
    main()
