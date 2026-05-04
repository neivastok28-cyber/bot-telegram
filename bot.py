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
if not REDIS_URL:
    raise ValueError("REDIS_URL belum diset")

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
user_data = {}
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

# ================= CACHE =================
def get_cache(nomor):
    data = r.get(f"cache:{nomor}")
    return json.loads(data) if data else None

def save_cache(nomor, nama, tags):
    r.setex(f"cache:{nomor}", 86400, json.dumps({
        "nama": nama,
        "tags": tags
    }))

# ================= WA CHECK =================
def cek_wa(nomor):
    try:
        url = f"https://wa.me/{nomor}"
        r = requests.get(url, timeout=5)
        return "invalid" not in r.text.lower()
    except:
        return False

# ================= API =================
def call_api(nomor):
    try:
        url = f"https://gcontact.id/api?token={API_TOKEN}&nomor={nomor}"
        return requests.get(url, timeout=10).json()
    except:
        return None

# ================= HISTORY =================
def save_history(uid, nomor, nama):
    cursor.execute("INSERT INTO history VALUES (NULL, ?, ?, ?, ?)",
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

# ================= TAG PAGE =================
def build_tag_page(tags, page, per_page=85):
    start = page * per_page
    chunk = tags[start:start + per_page]

    max_page = max(0, (len(tags)-1)//per_page)
    text = f"🏷️ Semua Tag (Page {page+1}/{max_page+1})\n\n"

    for i, t in enumerate(chunk, start=1 + start):
        name = html.escape(str(t.get("value", "-")))
        count = t.get("count", 0)
        text += f"{i}. {name} (<b>{count}</b>)\n"

    return text

def tag_keyboard(page, max_page, uid):
    btn = []
    nav = []

    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"tag:{uid}:{page-1}"))
    if page < max_page:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"tag:{uid}:{page+1}"))

    if nav:
        btn.append(nav)

    btn.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu:back")])
    return InlineKeyboardMarkup(btn)

# ================= MAIN =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Kirim nomor untuk cek 📱")

# ================= HANDLE =================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if not rate_limit(uid):
        return await update.message.reply_text("⏳ Tunggu...")

    nomor = format_nomor(text)

    if not nomor or len(nomor) < 10:
        return await update.message.reply_text("Kirim nomor yang valid")

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

    wa_status = "✅ Terdaftar" if cek_wa(nomor) else "❌ Tidak"

    # preview tag
    tag_text = ""
    for i, t in enumerate(tags[:10], start=1):
        name = html.escape(str(t.get("value", "-")))
        count = t.get("count", 0)
        tag_text += f"{i}. {name} (<b>{count}</b>)\n"

    user_data[uid] = {"tags": tags, "nama": nama, "nomor": nomor}

    text_out = f"""📱 {nomor}
💬 {wa_link(nomor)}

📲 WhatsApp: {wa_status}

👤 {nama}
📊 {len(tags)} tag

🏷️ Tag:
{tag_text if tag_text else 'Tidak ada tag'}"""

    await msg.edit_text(
        text_out,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏷️ Semua Tag", callback_data=f"showtag:{uid}")],
            [InlineKeyboardButton("📜 History", callback_data="menu:history"),
             InlineKeyboardButton("📊 Statistik", callback_data="menu:stats")]
        ]),
        parse_mode="HTML"
    )

# ================= CALLBACK =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data.split(":")
    uid = update.effective_user.id

    if data[0] == "showtag":
        uid = int(data[1])
        data_user = user_data.get(uid)
        if not data_user:
            return

        tags = data_user["tags"]
        page = 0
        max_page = max(0, (len(tags)-1)//85)

        return await q.edit_message_text(
            build_tag_page(tags, page),
            reply_markup=tag_keyboard(page, max_page, uid),
            parse_mode="HTML"
        )

    if data[0] == "tag":
        uid = int(data[1])
        page = int(data[2])

        data_user = user_data.get(uid)
        if not data_user:
            return

        tags = data_user["tags"]
        max_page = max(0, (len(tags)-1)//85)

        return await q.edit_message_text(
            build_tag_page(tags, page),
            reply_markup=tag_keyboard(page, max_page, uid),
            parse_mode="HTML"
        )

    if data[0] == "menu":
        if data[1] == "history":
            page = 0
            total = count_history(uid)
            max_page = max(0, (total-1)//20)
            rows = get_history(uid, page)

            text = "📜 History:\n\n"
            for i, (nomor, nama, ts) in enumerate(rows, start=1):
                t = time.strftime('%d-%m %H:%M', time.localtime(ts))
                text += f"{i}. {nomor}\n👤 {nama}\n🕒 {t}\n\n"

            return await q.edit_message_text(text)

        if data[1] == "stats":
            cursor.execute("SELECT total_check FROM stats WHERE user_id=?", (uid,))
            row = cursor.fetchone()
            total = row[0] if row else 0

            return await q.edit_message_text(f"📊 Total cek: {total}")

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.add_handler(CallbackQueryHandler(button))

print("BOT RUNNING...")
app.run_polling(drop_pending_updates=True)
