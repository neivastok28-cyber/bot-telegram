import logging
import os
import re
import time
import html
import sqlite3
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

if not TOKEN:
    raise ValueError("TOKEN tidak ditemukan di environment (Railway/GitHub)")
if not API_TOKEN:
    raise ValueError("API_TOKEN tidak ditemukan di environment (Railway/GitHub)")

# ================= DB =================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS cache (
    nomor TEXT PRIMARY KEY,
    nama TEXT,
    tags TEXT,
    updated_at INTEGER
)
""")
conn.commit()

# ================= MEMORY =================
user_data = {}
user_last = {}

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

# ================= WA LINK =================
def wa_link(n):
    return f"https://wa.me/{n}"

# ================= CACHE =================
def save_cache(nomor, nama, tags):
    raw = "\n".join([f"{t.get('value','')}||{t.get('count',0)}" for t in tags])

    cursor.execute("""
    INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?)
    """, (nomor, nama, raw, int(time.time())))
    conn.commit()

def get_cache(nomor):
    cursor.execute("SELECT nama, tags FROM cache WHERE nomor=?", (nomor,))
    return cursor.fetchone()

# ================= API =================
def call_api(nomor):
    try:
        url = f"https://gcontact.id/api?token={API_TOKEN}&nomor={nomor}"
        r = requests.get(url, timeout=10)
        data = r.json()

        print("DEBUG API RESPONSE:", data)  # penting untuk Railway log

        return data
    except Exception as e:
        print("API ERROR:", e)
        return None

# ================= PAGINATION =================
def build_page(tags, page, per_page=85):
    start = page * per_page
    chunk = tags[start:start + per_page]

    text = "🏷️ Semua Tag:\n\n"

    for i, t in enumerate(chunk, start=1 + start):
        name = html.escape(str(t.get("value", "-")))
        count = int(t.get("count", 0) or 0)
        text += f"{i}. {name} >> <b>{count} Tag</b>\n"

    return text

def build_keyboard(page, max_page, uid):
    btn = []

    if page > 0:
        btn.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{uid}:{page-1}"))

    if page < max_page:
        btn.append(InlineKeyboardButton("Next ➡️", callback_data=f"{uid}:{page+1}"))

    return InlineKeyboardMarkup([btn])

# ================= HANDLE MESSAGE =================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id
    text = update.message.text

    if not rate_limit(uid):
        await update.message.reply_text("⏳ terlalu cepat")
        return

    msg = await update.message.reply_text("🔍 mencari data...")

    nomor = format_nomor(text)
    if not nomor:
        await msg.edit_text("❌ nomor tidak valid")
        return

    cached = get_cache(nomor)

    if cached:
        nama, tags_raw = cached
        tags = []

        if tags_raw:
            for x in tags_raw.split("\n"):
                if "||" in x:
                    v, c = x.split("||", 1)
                    tags.append({"value": v, "count": int(c or 0)})

    else:
        data = call_api(nomor)

        # FIX UTAMA: jangan terlalu ketat cek "success"
        if not data:
            await msg.edit_text("❌ API tidak merespon")
            return

        gc = data.get("data", {}).get("getcontact")

        if not gc:
            await msg.edit_text("❌ data tidak ditemukan / API berubah")
            return

        nama = gc.get("primary", "-")
        tags = gc.get("tags", [])

        save_cache(nomor, nama, tags)

    page = 0
    max_page = max(0, (len(tags) - 1) // 85)

    user_data[uid] = {"tags": tags, "nama": nama, "nomor": nomor}

    text_msg = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag

{build_page(tags, page)}"""

    await msg.edit_text(
        text_msg,
        reply_markup=build_keyboard(page, max_page, uid),
        parse_mode="HTML"
    )

# ================= CALLBACK =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    uid, page = q.data.split(":")
    uid = int(uid)
    page = int(page)

    data = user_data.get(uid)
    if not data:
        return

    tags = data["tags"]
    nama = data["nama"]
    nomor = data["nomor"]

    max_page = max(0, (len(tags) - 1) // 85)

    text_msg = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag

{build_page(tags, page)}"""

    await q.edit_message_text(
        text_msg,
        reply_markup=build_keyboard(page, max_page, uid),
        parse_mode="HTML"
    )

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot aktif 🚀")

# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.add_handler(CallbackQueryHandler(button))

print("BOT RUNNING...")
app.run_polling()
