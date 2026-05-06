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
GC_TOKENS = os.getenv("API_TOKENS", "").split(",")
REDIS_URL = os.getenv("REDIS_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

r = redis.Redis.from_url(REDIS_URL, decode_responses=True) if REDIS_URL else None
logging.basicConfig(level=logging.INFO)

session = None
token_index = 0
# ================= TAMBAHAN ANTI LIMIT =================
def is_rate_limited(user_id):
    if not r:
        return False
    key = f"rate:{user_id}"
    val = r.get(key)
    if val and int(val) >= 5:
        return True
    r.incr(key)
    r.expire(key, 10)
    return False


def acquire_lock(number):
    if not r:
        return True
    return r.set(f"lock:{number}", "1", nx=True, ex=10)


def release_lock(number):
    if r:
        r.delete(f"lock:{number}")


# ================= UI =================
def main_menu(user_id):
    buttons = [
        [InlineKeyboardButton("🔍 Cek Nomor", callback_data="check")],
        [
            InlineKeyboardButton("👤 Profile", callback_data="profile"),
            InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
        ],
        [InlineKeyboardButton("🎟 Quota", callback_data="quota")],
        [
            InlineKeyboardButton("📜 History", callback_data="history"),
            InlineKeyboardButton("📥 Export", callback_data="export"),
        ],
        [InlineKeyboardButton("🗑 Clear", callback_data="clear")],
    ]

    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")])

    return InlineKeyboardMarkup(buttons)


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
def get_next_token():
    global token_index

    if not GC_TOKENS:
        return None

    token = GC_TOKENS[token_index % len(GC_TOKENS)]
    token_index += 1

    return token


async def get_gcontact(number):
    for _ in range(len(GC_TOKENS)):  # coba semua token
        token = get_next_token()

        if not token:
            return {}

        url = f"https://gcontact.id/api?token={token}&nomor={number}"

        try:
            async with session.get(url, timeout=10) as res:
                data = await res.json()
                print("====== DEBUG API ======")
                print("TOKEN:", token)
                print("NUMBER:", number)
                print("RESPONSE:", data)
                print("TAGS RAW:", data.get("data", {}).get("getcontact", {}).get("tags"))
                print("PICTURE:", picture)

                if data.get("success") and data.get("data"):
                    return data

        except Exception as e:
            print("ERROR:", e)

        await asyncio.sleep(1)

    print("SEMUA TOKEN GAGAL")
    return {}

# ================= TAG =================
def normalize_tag(text):
    text = text.strip()
    text = text.strip()
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
        count = t.get("count", 1)
        
        if val:
            cleaned.extend([normalize_tag(val)] * count)

    return Counter(cleaned).most_common()


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

def merge_with_alias(tags):
    groups = []

    for tag, count in tags:
        found = False

        for g in groups:
            if is_similar_name(tag, g["key"]):
                g["count"] += count
                g["aliases"].append(tag)
                found = True
                break

        if not found:
            groups.append({
                "key": tag,
                "count": count,
                "aliases": [tag]
            })

    return sorted(
        groups,
        key=lambda x: x["count"],
        reverse=True
    )


# ================= FORMAT =================
def format_display(tag):
    words = tag.split()

    if words and words[0] == "bg":
        return "Bg " + " ".join(w.title() for w in words[1:])
    if words and words[0] == "bang":
        return "Bang " + " ".join(w.title() for w in words[1:])

    return " ".join(w.title() for w in words)


# ================= ANALYZE =================
def analyze_tags(tags):

    dominant = tags[0][0]
    dominant_count = tags[0][1]

    alias = []
    for t, _ in tags[1:10]:
        if len(t.split()) == 1:
            alias.append(t)

    alias = " / ".join(set(alias)) if alias else "-"

    lokasi = "-"
    lokasi_list = ["jakarta", "bengkulu", "bandung", "surabaya", "medan"]

    for t, _ in tags:
        for loc in lokasi_list:
            if loc in t:
                lokasi = loc.title()
                break

    return f"{format_display(dominant)} ({dominant_count})", alias.title(), lokasi


# ================= MENU =================
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = update.effective_user.id

    # 🔐 Safe ambil data (biar gak error kalau redis mati)
    try:
        quota = get_quota(user_id)
        usage = get_usage(user_id)
    except:
        quota = 0
        usage = 0

    if q.data == "back":
        return await q.edit_message_text(
            f"🤖 MENU UTAMA\n\n🎟 Quota: {quota}\n📊 Usage: {usage}",
            reply_markup=main_menu(user_id)
        )

    if q.data == "check":
        return await q.edit_message_text("📱 Kirim nomor")

    if q.data == "quota":
        return await q.edit_message_text(
            f"🎟 Sisa Quota: {quota}",
            reply_markup=back_button()
        )

    if q.data == "profile":
        return await q.edit_message_text(
            f"👤 PROFILE\n\n"
            f"ID: {user_id}\n"
            f"🎟 Quota: {quota}\n"
            f"📊 Usage: {usage}",
            reply_markup=back_button()
        )

    if q.data == "dashboard":
        total_users = len(r.keys("quota:*")) if r else 0
        return await q.edit_message_text(
            f"📊 DASHBOARD\nUsers: {total_users}",
            reply_markup=back_button()
        )

    if q.data == "history":
        raw = r.lrange(f"history:{user_id}", 0, 10) if r else []
        text = "\n".join([json.loads(x)["number"] for x in raw]) or "-"
        return await q.edit_message_text(text, reply_markup=back_button())

    if q.data == "export":
        return await export_history(update, context)

    if q.data == "clear":
        if r:
            r.delete(f"history:{user_id}")
        return await q.edit_message_text("🗑 History dihapus", reply_markup=back_button())

    if q.data == "admin":
        return await q.edit_message_text(
            "⚙️ ADMIN PANEL\n"
            "/addquota <id> <jumlah>\n"
            "/setquota <id> <jumlah>",
            reply_markup=back_button()
        )

# ================= ADMIN COMMAND =================
async def setquota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ bukan admin")

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        set_quota(user_id, amount)
        await update.message.reply_text("✅ quota diset")
    except:
        await update.message.reply_text("❌ format: /setquota id jumlah")


async def addquota_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ bukan admin")

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        add_quota(user_id, amount)
        await update.message.reply_text("✅ quota ditambah")
    except:
        await update.message.reply_text("❌ format: /addquota id jumlah")


# ================= EXPORT =================
async def export_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    raw = r.lrange(f"history:{user_id}", 0, 9999) if r else []

    wb = Workbook()
    ws = wb.active
    ws.append(["No", "Nomor", "Nama", "Total Tag"])

    for i, item in enumerate(raw, start=1):
        obj = json.loads(item)
        ws.append([i, obj["number"], obj["name"], len(obj["tags"])])

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    await context.bot.send_document(chat_id=user_id, document=bio, filename="history.xlsx")


# ================= HANDLE =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # 🔒 Anti spam
    if is_rate_limited(user_id):
        return await update.message.reply_text("⚠️ Terlalu cepat")

    # 🎟 Cek quota
    if not use_quota(user_id):
        return await update.message.reply_text("❌ quota habis")

    # 📱 Format nomor
    number = format_number(update.message.text)
    if not number:
        return await update.message.reply_text("❌ nomor tidak valid")

    # 🔐 Lock biar tidak double request
    if not acquire_lock(number):
        return await update.message.reply_text("⏳ Nomor sedang diproses...")

    # 🔎 Loading
    loading = await update.message.reply_text("🔎 mencari...")

    try:
        # 🔁 Cache / API
        cached = get_cache(number)
        data = cached if cached else await get_gcontact(number)
        picture = data.get("data", {}).get("getcontact", {}).get("picture", None)

        if not data:
            return await loading.edit_text("⚠️ API error")

        if not cached:
            set_cache(number, data)

        add_usage(user_id)

        # 🏷 Tag processing
        tags_raw_counter = extract_tags(data)
        tags = sorted(tags_raw_counter, key=lambda x: (-x[1], x[0]))
        groups = []

        raw_list = []
        for t, c in tags_raw_counter:
            raw_list.extend([t] * c)

        dominant, alias, lokasi = analyze_tags(tags)

        # 📜 History
        if r:
            r.lpush(f"history:{user_id}", json.dumps({
                "number": number,
                "name": dominant,
                "tags": tags
            }))
            r.ltrim(f"history:{user_id}", 0, 50)

        # 💾 Simpan ke session
        context.user_data["tags"] = tags
        context.user_data["groups"] = []
        context.user_data["raw"] = raw_list
        context.user_data["number"] = number
        context.user_data["picture"] = picture

        # 🖥 Render
        await render_page(update, context, loading)

    finally:
        # 🔓 WAJIB → biar bot tidak freeze
        release_lock(number)

# ================= RENDER =================
async def render_page(update, context, msg_obj):
    picture = context.user_data.get("picture", None)
    tags = context.user_data["tags"]
    number = context.user_data["number"]

    dominant, alias, lokasi = analyze_tags(tags)

    # format list
    lines = [
        f"• {html.escape(format_display(t))} <b>({c})</b>"
        for t, c in tags
    ]

    MAX_LINES = 85

    chunks = [
        lines[i:i + MAX_LINES]
        for i in range(0, len(lines), MAX_LINES)
    ]

    if picture:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=picture,
                caption="👤 Profile Photo"
            )
        except Exception as e:
            print("PHOTO ERROR:", e)

    # HEADER
    await msg_obj.edit_text(
        f"""☎️ <b>Contact List</b>

    👤 <b>{html.escape(dominant.split('(')[0])}</b> (Primary)

    📞 <b>Whatsapp</b>
    Terdaftar
    <a href="https://wa.me/{number}">https://wa.me/{number}</a>
    """,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    # LIST
    for chunk in chunks:
        text = "📌 <b>Tag List</b>\n\n" + "\n".join(chunk)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="HTML"
        )
        
# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 MENU UTAMA", reply_markup=main_menu(update.effective_user.id))


# ================= INIT =================
async def init(app):
    global session
    session = aiohttp.ClientSession()

async def shutdown(app):
    global session
    if session:
        await session.close()


# ================= MAIN =================
def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(init)
        .post_shutdown(shutdown)
        .build()
    )

    # ================= COMMAND =================
    app.add_handler(CommandHandler("start", start))

    # 🔥 pakai handler yang benar
    app.add_handler(CommandHandler("setquota", setquota_cmd))
    app.add_handler(CommandHandler("addquota", addquota_cmd))

    # 🔥 baru menu
    app.add_handler(CallbackQueryHandler(menu))

    # ================= MESSAGE =================
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 BOT FINAL SUPER PERFECT")
    app.run_polling()


if __name__ == "__main__":
    main()
