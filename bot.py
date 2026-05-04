import logging, os, re, time, html, json, threading, requests
from flask import Flask
from dotenv import load_dotenv
import redis

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ================= LOG =================
logging.basicConfig(level=logging.INFO)

# ================= KEEP ALIVE =================
app_web = Flask('')

@app_web.route('/')
def home():
    return "Bot is running!"

def run_web():
    app_web.run(host='0.0.0.0', port=8080)

threading.Thread(target=run_web).start()

# ================= ENV =================
load_dotenv()
TOKEN = os.getenv("TOKEN")
API_TOKEN = os.getenv("API_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")

# ================= REDIS =================
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

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

# ================= FORMAT =================
def format_nomor(n):
    n = re.sub(r"\D", "", n)
    if not n: return ""
    if n.startswith("0"): n = "62" + n[1:]
    elif n.startswith("8"): n = "62" + n
    elif not n.startswith("62"): n = "62" + n
    return n

def wa_link(n):
    return f"https://wa.me/{n}"

# ================= API =================
def call_api(nomor):
    try:
        url = f"https://gcontact.id/api?token={API_TOKEN}&nomor={nomor}"
        return requests.get(url, timeout=10).json()
    except:
        return None

# ================= CACHE =================
def get_cache(nomor):
    data = r.get(f"cache:{nomor}")
    return json.loads(data) if data else None

def save_cache(nomor, data):
    r.setex(f"cache:{nomor}", 86400, json.dumps(data))

# ================= HISTORY =================
def save_history(uid, nomor):
    r.lpush(f"history:{uid}", nomor)
    r.ltrim(f"history:{uid}", 0, 99)

def get_history(uid, page=0, per_page=20):
    data = r.lrange(f"history:{uid}", page*per_page, (page+1)*per_page-1)
    return data

# ================= BUILD TAG =================
def build_tags(tags, page, per_page=85):
    start = page * per_page
    chunk = tags[start:start+per_page]

    text = "🏷️ Semua Tag:\n\n"
    for i, t in enumerate(chunk, start=1+start):
        name = html.escape(str(t.get("value", "-")))
        count = int(t.get("count", 0) or 0)
        text += f"{i}. {name} (<b>{count}</b>)\n"
    return text

# ================= KEYBOARD =================
def menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 History", callback_data="history:0")]
    ])

def paging_keyboard(uid, page, max_page):
    btn = []
    if page > 0:
        btn.append(InlineKeyboardButton("⬅️", callback_data=f"tag:{uid}:{page-1}"))
    if page < max_page:
        btn.append(InlineKeyboardButton("➡️", callback_data=f"tag:{uid}:{page+1}"))
    return InlineKeyboardMarkup([btn])

def history_keyboard(page, max_page):
    btn = []
    if page > 0:
        btn.append(InlineKeyboardButton("⬅️", callback_data=f"history:{page-1}"))
    if page < max_page:
        btn.append(InlineKeyboardButton("➡️", callback_data=f"history:{page+1}"))
    return InlineKeyboardMarkup([btn])

# ================= HANDLE =================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    if not rate_limit(uid):
        await update.message.reply_text("⏳ terlalu cepat")
        return

    nomor = format_nomor(text)
    if not nomor:
        await update.message.reply_text("❌ kirim nomor valid")
        return

    msg = await update.message.reply_text("🔍 mencari...")

    cached = get_cache(nomor)

    if cached:
        nama = cached["nama"]
        tags = cached["tags"]
    else:
        data = call_api(nomor)
        gc = data.get("data", {}).get("getcontact") if data else None

        if not gc:
            await msg.edit_text("❌ data tidak ditemukan")
            return

        nama = gc.get("primary", "-")
        tags = gc.get("tags", [])
        save_cache(nomor, {"nama": nama, "tags": tags})

    save_history(uid, nomor)

    page = 0
    max_page = max(0, (len(tags)-1)//85)

    user_data[uid] = {"tags": tags, "nama": nama, "nomor": nomor}

    text_msg = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag

{build_tags(tags, page)}
"""

    await msg.edit_text(
        text_msg,
        reply_markup=paging_keyboard(uid, page, max_page),
        parse_mode="HTML"
    )

# ================= CALLBACK =================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data

    if data.startswith("tag:"):
        _, uid, page = data.split(":")
        uid = int(uid)
        page = int(page)

        d = user_data.get(uid)
        if not d: return

        tags = d["tags"]
        nama = d["nama"]
        nomor = d["nomor"]

        max_page = max(0, (len(tags)-1)//85)

        text_msg = f"""📱 {nomor}
💬 {wa_link(nomor)}

👤 {nama}
📊 {len(tags)} tag

{build_tags(tags, page)}
"""

        await q.edit_message_text(
            text_msg,
            reply_markup=paging_keyboard(uid, page, max_page),
            parse_mode="HTML"
        )

    elif data.startswith("history:"):
        page = int(data.split(":")[1])
        uid = q.from_user.id

        hist = get_history(uid, page)
        if not hist:
            await q.edit_message_text("📭 kosong")
            return

        text = "📜 History:\n\n"
        for i, n in enumerate(hist, start=1):
            text += f"{i}. {n}\n"

        await q.edit_message_text(
            text,
            reply_markup=history_keyboard(page, page+1),
            parse_mode="HTML"
        )

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot Premium Ready\n\nKirim nomor langsung.",
        reply_markup=menu_keyboard()
    )

# ================= MAIN =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.add_handler(CallbackQueryHandler(button))

print("BOT RUNNING...")
app.run_polling(drop_pending_updates=True)
