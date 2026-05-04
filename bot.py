import logging
logging.basicConfig(level=logging.INFO)

import os, re, time, html, sqlite3, requests, json, redis
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

# ================= ENV =================
load_dotenv()
TOKEN = os.getenv("TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

if not TOKEN or not API_TOKEN:
    raise ValueError("TOKEN / API_TOKEN belum diset")

# ================= REDIS =================
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ================= DB =================
conn = sqlite3.connect("bot.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""CREATE TABLE IF NOT EXISTS history (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER,
nomor TEXT,
nama TEXT,
created_at INTEGER)""")

cursor.execute("""CREATE TABLE IF NOT EXISTS stats (
user_id INTEGER PRIMARY KEY,
total_check INTEGER DEFAULT 0,
last_check INTEGER)""")

conn.commit()

# ================= MEMORY =================
user_mode = {}
user_last = {}

# ================= UTIL =================
def rate_limit(uid, delay=2):
    now = time.time()
    if now - user_last.get(uid, 0) < delay:
        return False
    user_last[uid] = now
    return True

def format_nomor(n):
    n = re.sub(r"\D", "", n)
    if n.startswith("0"):
        n = "62" + n[1:]
    elif n.startswith("8"):
        n = "62" + n
    return n

def wa_link(n):
    return f"https://wa.me/{n}"

# ================= CACHE REDIS =================
def get_cache(nomor):
    data = r.get(f"cache:{nomor}")
    return json.loads(data) if data else None

def save_cache(nomor, nama, tags):
    r.setex(f"cache:{nomor}", 86400, json.dumps({
        "nama": nama,
        "tags": tags
    }))

# ================= API =================
def call_api(nomor):
    try:
        url = f"https://gcontact.id/api?token={API_TOKEN}&nomor={nomor}"
        return requests.get(url, timeout=10).json()
    except:
        return None

# ================= HISTORY =================
def save_history(uid, nomor, nama):
    cursor.execute("INSERT INTO history (user_id, nomor, nama, created_at) VALUES (?, ?, ?, ?)",
                   (uid, nomor, nama, int(time.time())))
    conn.commit()

def get_history(uid, page=0, per_page=20):
    offset = page * per_page
    cursor.execute("SELECT nomor, nama, created_at FROM history WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                   (uid, per_page, offset))
    return cursor.fetchall()

def count_history(uid):
    cursor.execute("SELECT COUNT(*) FROM history WHERE user_id=?", (uid,))
    return cursor.fetchone()[0]

# ================= STATS =================
def update_stats(uid):
    cursor.execute("""
    INSERT INTO stats (user_id, total_check, last_check)
    VALUES (?, 1, ?)
    ON CONFLICT(user_id) DO UPDATE SET
    total_check = total_check + 1,
    last_check = excluded.last_check
    """, (uid, int(time.time())))
    conn.commit()

# ================= UI =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Cek Nomor", callback_data="menu:cek")],
        [InlineKeyboardButton("📜 History", callback_data="menu:history")],
        [InlineKeyboardButton("📊 Statistik", callback_data="menu:stats")]
    ])

def history_text(rows, page):
    text = "📜 History:\n\n"
    if not rows:
        return "Belum ada history"

    for i, (nomor, nama, ts) in enumerate(rows, start=1 + page*20):
        t = time.strftime('%d-%m %H:%M', time.localtime(ts))
        text += f"{i}. {nomor}\n👤 {nama}\n🕒 {t}\n\n"
    return text

def history_keyboard(page, max_page, uid):
    btn = []
    nav = []

    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"his:{uid}:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"his:{uid}:{page+1}"))

    if nav:
        btn.append(nav)

    btn.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu:back")])
    return InlineKeyboardMarkup(btn)

# ================= HANDLER =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Menu utama:", reply_markup=main_menu())

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not rate_limit(uid):
        return await update.message.reply_text("⏳ Tunggu...")

    if user_mode.get(uid) != "cek":
        return await update.message.reply_text("Pilih menu dulu", reply_markup=main_menu())

    nomor = format_nomor(update.message.text)

    if not nomor:
        return await update.message.reply_text("Nomor tidak valid")

    msg = await update.message.reply_text("🔍 mencari...")

    data = get_cache(nomor)

    if data:
        nama, tags = data["nama"], data["tags"]
    else:
        res = call_api(nomor)
        if not res:
            return await msg.edit_text("API error")

        gc = res.get("data", {}).get("getcontact")
        if not gc:
            return await msg.edit_text("Data tidak ditemukan")

        nama = gc.get("primary", "-")
        tags = gc.get("tags", [])

        save_cache(nomor, nama, tags)

    save_history(uid, nomor, nama)
    update_stats(uid)

    text = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag"""

    await msg.edit_text(text, reply_markup=main_menu())

# ================= CALLBACK =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    uid = update.effective_user.id

    if data[0] == "menu":

        if data[1] == "cek":
            user_mode[uid] = "cek"
            return await q.edit_message_text("Kirim nomor:", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Menu", callback_data="menu:back")]
            ]))

        if data[1] == "history":
            page = 0
            total = count_history(uid)
            max_page = max(0, (total-1)//20)
            rows = get_history(uid, page)

            return await q.edit_message_text(
                history_text(rows, page),
                reply_markup=history_keyboard(page, max_page, uid)
            )

        if data[1] == "stats":
            cursor.execute("SELECT total_check FROM stats WHERE user_id=?", (uid,))
            row = cursor.fetchone()
            total = row[0] if row else 0

            return await q.edit_message_text(
                f"📊 Total cek kamu: {total}",
                reply_markup=main_menu()
            )

        if data[1] == "back":
            user_mode[uid] = None
            return await q.edit_message_text("Menu utama:", reply_markup=main_menu())

    if data[0] == "his":
        page = int(data[2])

        total = count_history(uid)
        max_page = max(0, (total-1)//20)
        rows = get_history(uid, page)

        return await q.edit_message_text(
            history_text(rows, page),
            reply_markup=history_keyboard(page, max_page, uid)
        )

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.add_handler(CallbackQueryHandler(button))

print("BOT RUNNING...")
app.run_polling(drop_pending_updates=True)
