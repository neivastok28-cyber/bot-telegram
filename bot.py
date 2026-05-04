import logging
import os
import re
import time
import html
import requests

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ================= LOG =================
logging.basicConfig(level=logging.INFO)

# ================= ENV =================
load_dotenv()

TOKEN = os.getenv("TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

if not TOKEN:
    raise ValueError("TOKEN tidak ditemukan")
if not API_TOKEN:
    raise ValueError("API_TOKEN tidak ditemukan")

# ================= REDIS (SAFE MODE) =================
try:
    import redis
    if REDIS_URL:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    else:
        r = None
except:
    r = None

# ================= MEMORY =================
user_last = {}
user_data = {}

# ================= RATE LIMIT =================
def rate_limit(uid, delay=2):
    now = time.time()
    if now - user_last.get(uid, 0) < delay:
        return False
    user_last[uid] = now
    return True

# ================= FORMAT NOMOR =================
def format_nomor(n):
    n = re.sub(r"\D", "", n)
    if not n:
        return ""

    if n.startswith("0"):
        n = "62" + n[1:]
    elif n.startswith("8"):
        n = "62" + n
    elif not n.startswith("62"):
        n = "62" + n

    return n

def wa_link(n):
    return f"https://wa.me/{n}"

# ================= CACHE =================
def save_cache(nomor, nama, tags):
    if r:
        r.setex(f"cache:{nomor}", 86400, str({"nama": nama, "tags": tags}))

def get_cache(nomor):
    if r:
        data = r.get(f"cache:{nomor}")
        if data:
            return eval(data)
    return None

# ================= HISTORY =================
def save_history(uid, nomor):
    if r:
        r.lpush(f"history:{uid}", nomor)

def get_history(uid):
    if r:
        return r.lrange(f"history:{uid}", 0, 100)
    return []

# ================= STAT =================
def add_stat(uid):
    if r:
        r.incr(f"stat:{uid}")

def get_stat(uid):
    if r:
        return int(r.get(f"stat:{uid}") or 0)
    return 0

# ================= API =================
def call_api(nomor):
    try:
        url = f"https://gcontact.id/api?token={API_TOKEN}&nomor={nomor}"
        return requests.get(url, timeout=10).json()
    except:
        return None

# ================= TAG PAGING =================
def build_tags(tags, page, per_page=85):
    start = page * per_page
    chunk = tags[start:start + per_page]

    text = "🏷️ Semua Tag:\n\n"
    for i, t in enumerate(chunk, start=1 + start):
        name = html.escape(str(t.get("value", "-")))
        count = int(t.get("count", 0))
        text += f"{i}. {name} <b>({count})</b>\n"

    return text

# ================= HISTORY PAGING =================
def build_history(data, page, per_page=20):
    start = page * per_page
    chunk = data[start:start + per_page]

    text = "📜 History:\n\n"
    for i, n in enumerate(chunk, start=1 + start):
        text += f"{i}. {n}\n"

    return text

# ================= KEYBOARD =================
def keyboard(page, max_page, prefix):
    btn = []

    if page > 0:
        btn.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}:{page-1}"))

    if page < max_page:
        btn.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}:{page+1}"))

    return InlineKeyboardMarkup([btn])

# ================= HANDLE =================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id
    text = update.message.text

    if not rate_limit(uid):
        return await update.message.reply_text("⏳ tunggu...")

    nomor = format_nomor(text)
    if not nomor:
        return await update.message.reply_text("❌ nomor tidak valid")

    msg = await update.message.reply_text("🔍 mencari...")

    cache = get_cache(nomor)

    if cache:
        nama = cache["nama"]
        tags = cache["tags"]
    else:
        data = call_api(nomor)

        if not data:
            return await msg.edit_text("❌ API error")

        gc = data.get("data", {}).get("getcontact")
        if not gc:
            return await msg.edit_text("❌ tidak ditemukan")

        nama = gc.get("primary", "-")
        tags = gc.get("tags", [])

        save_cache(nomor, nama, tags)

    save_history(uid, nomor)
    add_stat(uid)

    page = 0
    max_page = max(0, (len(tags) - 1) // 85)

    user_data[uid] = {"tags": tags, "nomor": nomor, "nama": nama}

    text_msg = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag

{build_tags(tags, page)}"""

    await msg.edit_text(
        text_msg,
        parse_mode="HTML",
        reply_markup=keyboard(page, max_page, f"tag:{uid}")
    )

# ================= BUTTON =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    action = data[0]

    if action == "tag":
        uid = int(data[1])
        page = int(data[2])

        d = user_data.get(uid)
        if not d:
            return

        tags = d["tags"]
        nomor = d["nomor"]
        nama = d["nama"]

        max_page = max(0, (len(tags) - 1) // 85)

        text_msg = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag

{build_tags(tags, page)}"""

        await q.edit_message_text(
            text_msg,
            parse_mode="HTML",
            reply_markup=keyboard(page, max_page, f"tag:{uid}")
        )

    elif action == "history":
        uid = int(data[1])
        page = int(data[2])

        hist = get_history(uid)
        max_page = max(0, (len(hist) - 1) // 20)

        await q.edit_message_text(
            build_history(hist, page),
            reply_markup=keyboard(page, max_page, f"history:{uid}")
        )

    elif action == "stat":
        uid = update.effective_user.id
        total = get_stat(uid)

        await q.edit_message_text(f"📊 Total cek kamu: {total}")

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id

    kb = [
        [InlineKeyboardButton("📜 History", callback_data=f"history:{uid}:0")],
        [InlineKeyboardButton("📊 Statistik", callback_data="stat")]
    ]

    await update.message.reply_text(
        "🤖 Bot aktif\n\nKetik nomor langsung untuk cek",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.add_handler(CallbackQueryHandler(button))

print("BOT RUNNING...")
app.run_polling()
