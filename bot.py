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
GC_TOKENS = [
    t.strip()
    for t in os.getenv("API_TOKENS", "").split(",")
    if t.strip()
]
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
STOPWORDS = [
    "mobil","motor","jual","toko","shop","akun",
    "bisnis","sales","showroom","pt","cv","admin","wa",
    "bekasi","jakarta","bandung","surabaya","medan"
]
def is_human_name(text):
    words = text.lower().split()

    # buang keyword bisnis
    for w in words:
        if w in STOPWORDS:
            return False

    # minimal 2 kata (nama manusia)
    if len(words) < 2:
        return False

    # max 3 kata (biar bukan kalimat)
    if len(words) > 3:
        return False

    # buang angka
    if any(char.isdigit() for char in text):
        return False

    return True
    
def format_number(text):
    number = re.sub(r"\D", "", text)
    if number.startswith("0"):
        number = "62" + number[1:]
    if number.startswith("62") and len(number) >= 10:
        return number
    return None

def generate_number_formats(number):

    raw = number.strip()

    clean = re.sub(r"\D", "", raw)

    formats = []

    # 08xxxx
    if clean.startswith("08"):

        formats.append(clean)
        formats.append("62" + clean[1:])
        formats.append("+62" + clean[1:])

    # 62xxxx
    elif clean.startswith("62"):

        formats.append(clean)
        formats.append("0" + clean[2:])
        formats.append("+62" + clean[2:])

    else:
        formats.append(clean)

    return list(dict.fromkeys(formats))

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

    number_formats = generate_number_formats(number)

    clean = re.sub(r"\D", "", number)

    extra_formats = [
        clean,
        f"0{clean[2:]}" if clean.startswith("62") else clean,
        f"+{clean}" if not clean.startswith("+") else clean,
    ]

    number_formats.extend(extra_formats)

    # hapus duplicate
    number_formats = list(dict.fromkeys(number_formats))

    print("FORMATS:", number_formats)

    if not GC_TOKENS:
        print("TOKEN KOSONG")
        return {}

    # ================= LOOP TOKEN =================
    for _ in range(len(GC_TOKENS)):

        token = get_next_token()

        if not token:
            continue

        print(f"\n===== TRY TOKEN {token} =====")

        token_dead = False

        # ================= LOOP NOMOR =================
        for num in number_formats:

            url = f"https://gcontact.id/api?token={token}&nomor={num}"

            try:

                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as res:

                    # HTTP error
                    if res.status != 200:
                        print(f"HTTP ERROR {res.status} | {num}")
                        continue

                    # parse json
                    try:
                        data = await res.json()
                    except Exception:
                        text = await res.text()
                        print("INVALID JSON:", text[:300])
                        continue

                print("====== DEBUG API ======")
                print("TOKEN:", token)
                print("TRY NUMBER:", num)
                print("RESPONSE:", data)

                success = data.get("success", False)
                message = str(data.get("message", "")).lower()

                # ================= TOKEN HABIS =================
                if (
                    "empty quota" in message
                    or "quota" in message
                    or data.get("info_account", {}).get("remaining_quota") == 0
                ):

                    print(f"TOKEN QUOTA HABIS: {token}")

                    token_dead = True
                    break

                # ================= TOKEN INVALID =================
                if "invalid token" in message:

                    print(f"TOKEN INVALID: {token}")

                    token_dead = True
                    break

                # ================= SUCCESS =================
                if success:

                    result_data = data.get("data", {})

                    getcontact = result_data.get("getcontact", {})
                    whatsapp = result_data.get("whatsapp", {})
                    ewallet = result_data.get("ewallet", {})
                    search_engine = result_data.get("search_engine")

                    # ada tag / nama / foto
                    if (
                        getcontact.get("tags")
                        or getcontact.get("primary")
                        or getcontact.get("picture")
                    ):
                        print("SUCCESS GETCONTACT")
                        return data

                    # whatsapp valid
                    if whatsapp.get("exist") is True:
                        print("SUCCESS WHATSAPP")
                        return data

                    # ada ewallet
                    if ewallet:
                        print("SUCCESS EWALLET")
                        return data

                    # ada search engine
                    if search_engine:
                        print("SUCCESS SEARCH ENGINE")
                        return data

                    print("SUCCESS TAPI DATA KOSONG")

                else:

                    print(f"API FAILED: {message}")

            except asyncio.TimeoutError:

                print(f"REQUEST TIMEOUT: {num}")

            except aiohttp.ClientError as e:

                print(f"AIOHTTP ERROR: {str(e)}")

            except Exception as e:

                print(f"UNKNOWN ERROR: {str(e)}")

            await asyncio.sleep(1)

        # ================= PINDAH TOKEN =================
        if token_dead:
            continue

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
    best = None
    best_score = 0

    for t, c in tags:
        score = c

        if is_human_name(t):
            score += 20

        if score > best_score:
            best = t
            best_score = score

    dominant = best if best else tags[0][0]
    dominant_count = dict(tags).get(dominant, 1)

    return f"{format_display(dominant)} ({dominant_count})", "-", "-"

# ================= BACK BUTTON =================
def back_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="back")]
    ])

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
            "🤖 MENU UTAMA",
            reply_markup=main_menu(user_id)
        )
        
    elif q.data == "check":
        return await q.edit_message_text("📱 Kirim nomor")

    elif q.data == "quota":
        return await q.edit_message_text(
            f"🎟 Sisa Quota: {quota}",
            reply_markup=back_button()
        )

    elif q.data == "profile":
        return await q.edit_message_text(
            f"👤 PROFILE\n\n"
            f"ID: {user_id}\n"
            f"🎟 Quota: {quota}\n"
            f"📊 Usage: {usage}",
            reply_markup=back_button()
        )

    elif q.data == "dashboard":
        
        total_users = len(r.keys("quota:*")) if r else 0
        
        total_usage = sum(
            int(r.get(k) or 0)
            for k in r.keys("usage:*")
        ) if r else 0
        
        return await q.edit_message_text(
            f"""📊 DASHBOARD

    👥 Users: {total_users}
    📈 Total Usage: {total_usage}
    """,
            reply_markup=back_button()
        )

    elif q.data == "history":
        
        user_id = update.effective_user.id

        data = r.lrange(f"history:{user_id}", 0, 20) if r else []

        if not data:
            return await q.edit_message_text(
                "🪵 History kosong",
                reply_markup=back_button()
            )

        text = "🪵 HISTORY\n\n"

        for item in data:
            d = json.loads(item)

            text += f"• {d['number']} - {d['name']}\n"

        return await q.edit_message_text(
            text,
            reply_markup=back_button()
        )
    elif q.data == "export":

        user_id = update.effective_user.id

        data = r.lrange(f"history:{user_id}", 0, 100) if r else []

        if not data:
            return await q.answer("History kosong")

        text = ""

        for item in data:
           d = json.loads(item)

           text += f"{d['number']} - {d['name']}\n"

        with open("history.txt", "w", encoding="utf-8") as f:
            f.write(text)

        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=open("history.txt", "rb"),
            filename="history.txt"
        )
        
        return

    elif q.data == "clear":
    
        user_id = update.effective_user.id

        if r:
            r.delete(f"history:{user_id}")

        return await q.edit_message_text(
            "✅ History dihapus",
            reply_markup=back_button()
        )

    elif q.data == "admin":
    
        if update.effective_user.id != ADMIN_ID:
            return await q.answer("❌ admin only")

        users = len(r.keys("quota:*")) if r else 0

        text = f"""⚙️ ADMIN PANEL

    👥 Total Users: {users}

    🛠 COMMAND:

    /addquota id jumlah
    /setquota id jumlah
    """

        return await q.edit_message_text(
            text,
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

# ================= BULK SEARCH =================
async def handle_bulk_numbers(update, context, numbers):

    user_id = update.effective_user.id

    # max 50 nomor
    if len(numbers) > 50:
        return await update.message.reply_text(
            "❌ Maksimal 50 nomor sekali kirim"
        )

    # validasi quota
    quota = get_quota(user_id)

    if quota < len(numbers):
        return await update.message.reply_text(
            f"❌ Quota tidak cukup\n\n"
            f"Quota: {quota}\n"
            f"Butuh: {len(numbers)}"
        )

    loading = await update.message.reply_text(
        f"🔎 Memproses {len(numbers)} nomor..."
    )

    results = []

    # semaphore anti overload
    semaphore = asyncio.Semaphore(5)

    async def process_number(number):

        async with semaphore:

            try:

                number = format_number(number)

                if not number:
                    return f"❌ {number} | INVALID"

                cached = get_cache(number)

                data = cached if cached else await get_gcontact(number)

                if not data:
                    return f"❌ {number} | DATA TIDAK ADA"

                if not cached:
                    use_quota(user_id)
                    add_usage(user_id)

                primary = (
                    data.get("data", {})
                    .get("getcontact", {})
                    .get("primary")
                )

                tags = extract_tags(data)

                dominant, _, _ = analyze_tags(tags)
                
                tags = sorted(
                    tags,
                    key=lambda x: (-x[1], x[0])
                )

                # ================= NAMA =================
                if primary:
                    name = primary
                else:
                    name = dominant.split("(")[0].strip()
                # ================= DATA =================

                result_data = data.get("data", {})

                whatsapp = result_data.get("whatsapp", {})
                ewallet = result_data.get("ewallet", {})
                search_engine = result_data.get("search_engine")

                # ================= TAG =================
                tags_text = []

                for t, c in tags[:20]:

                    tag_name = format_display(t)

                    tags_text.append(
                        f"• {tag_name} ({c})"
                    )

                tag_result = "\n".join(tags_text)

                # ================= WHATSAPP =================
                wa_status = (
                    "Terdaftar"
                    if whatsapp.get("exist")
                    else "Tidak"
                )

                # ================= EWALLET =================
                ewallet_rows = []

                if ewallet.get("gopay_user"):
                    ewallet_rows.append(
                        f"Gopay: {ewallet.get('gopay_user')}"
                    )

                if ewallet.get("ovo"):
                     ewallet_rows.append(
                        f"OVO: {ewallet.get('ovo')}"
                    )

                if ewallet.get("dana"):
                    ewallet_rows.append(
                        f"Dana: {ewallet.get('dana')}"
                    )

                ewallet_text = "\n".join(ewallet_rows)

                return f"""
    ━━━━━━━━━━━━━━

    📞 {number}

    👤 Nama:
    {name}

    📱 Whatsapp:
    {wa_status}

    💳 Ewallet:
    {ewallet_text if ewallet_text else '-'}

    🌐 Search:
    {search_engine if search_engine else '-'}

    📌 Tag:
    {tag_result}

    ━━━━━━━━
    """

            except Exception as e:

                print("BULK ERROR:", str(e))

                return f"❌ {number} | ERROR"

    tasks = [process_number(n) for n in numbers]

    results = await asyncio.gather(*tasks)

    # save txt
    filename = f"bulk_{user_id}.txt"

    with open(filename, "w", encoding="utf-8") as f:

        f.write("\n".join(results))

    await loading.delete()

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=open(filename, "rb"),
        filename="result.txt",
        caption=f"✅ Selesai scan {len(numbers)} nomor"
    )
    
# ================= HANDLE =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    # anti spam
    if is_rate_limited(user_id):
        return await update.message.reply_text(
            "⚠️ Terlalu cepat"
        )

    text = update.message.text.strip()

    # ================= BULK MODE =================
    lines = text.splitlines()

    if len(lines) > 1:

        numbers = []

        for line in lines:

            num = format_number(line)

            if num:
                numbers.append(num)

        # hapus duplicate
        numbers = list(dict.fromkeys(numbers))

        if not numbers:
            return await update.message.reply_text(
                "❌ Tidak ada nomor valid"
            )

        return await handle_bulk_numbers(
            update,
            context,
            numbers
        )

    # ================= SINGLE MODE =================
    number = format_number(text)

    if not number:
        return await update.message.reply_text(
            "❌ nomor tidak valid"
        )

    # lock
    if not acquire_lock(number):
        return await update.message.reply_text(
            "⏳ Nomor sedang diproses..."
        )

    loading = await update.message.reply_text(
        f"🔎 Mencari:\n<code>{number}</code>",
        parse_mode="HTML",
        reply_to_message_id=update.message.message_id
    )

    try:

        cached = get_cache(number)

        data = cached if cached else await get_gcontact(number)

        user_quota = get_quota(user_id)

        gc_picture = (
            data.get("data", {})
            .get("getcontact", {})
            .get("picture")
        )

        wa_picture = (
            data.get("data", {})
            .get("whatsapp", {})
            .get("picture")
        )

        primary_name = (
            data.get("data", {})
            .get("getcontact", {})
            .get("primary")
        )

        if not data:
            return await loading.edit_text(
                "⚠️ Data tidak ditemukan / API bermasalah"
            )

        if not cached:

            if not use_quota(user_id):
                return await loading.edit_text(
                    "❌ quota habis"
                )

        if data and not cached:
            set_cache(number, data)

        if not cached:
            add_usage(user_id)

        tags_raw_counter = extract_tags(data)

        tags = sorted(
            tags_raw_counter,
            key=lambda x: (-x[1], x[0])
        )

        raw_list = []

        for t, c in tags_raw_counter:
            raw_list.extend([t] * c)

        dominant, alias, lokasi = analyze_tags(tags)

        # history
        if r:
            r.lpush(
                f"history:{user_id}",
                json.dumps({
                    "number": number,
                    "name": dominant,
                    "tags": tags
                })
            )

            r.ltrim(f"history:{user_id}", 0, 50)

        # session
        context.user_data["tags"] = tags
        context.user_data["groups"] = []
        context.user_data["raw"] = raw_list
        context.user_data["number"] = number

        context.user_data["ewallet"] = (
            data.get("data", {})
            .get("ewallet", {})
        )

        context.user_data["search_engine"] = (
            data.get("data", {})
            .get("search_engine")
        )

        context.user_data["primary_name"] = primary_name
        context.user_data["quota"] = user_quota
        context.user_data["gc_picture"] = gc_picture
        context.user_data["wa_picture"] = wa_picture

        await render_page(update, context, loading)

    finally:

        release_lock(number)

# ================= RENDER =================
async def render_page(update, context, msg_obj):
    primary_name = context.user_data.get("primary_name")
    quota = context.user_data.get("quota", 0)
    gc_picture = context.user_data.get("gc_picture", None)
    wa_picture = context.user_data.get("wa_picture", None)
    tags = context.user_data["tags"]
    number = context.user_data["number"]
    ewallet = context.user_data.get("ewallet", {})
    search_engine = context.user_data.get("search_engine")
    
    # ambil dominant dari tag
    dominant, alias, lokasi = analyze_tags(tags)

    # ambil count aman
    count = dominant.split("(")[-1].replace(")", "").strip()

    # 🔥 PRIORITAS API
    if primary_name:
        dominant = f"{primary_name} ({count})"
        dominant_name = primary_name.lower()
    else:
        dominant_name = dominant.split(" (")[0].lower()

    # ambil nama bersih
    display_name = dominant.split("(")[0].strip()
    
    # WA BLOCK (TANPA INDENT)
    wa_block = f"""📱 <b>Whatsapp</b> 〞
🟢 <b>Terdaftar</b>
<a href="https://wa.me/{number}">{number}</a>"""
    
    ewallet_text = ""

    if ewallet:

        gopay = ewallet.get("gopay_user")
        ovo = ewallet.get("ovo")
        dana = ewallet.get("dana")
        linkaja = ewallet.get("linkaja")
        shopeepay = ewallet.get("shopeepay")
        isaku = ewallet.get("isaku")

        rows = []

        if gopay and gopay != "UNREGISTERED":
            rows.append(f"• Gopay: <b>{gopay}</b>")

        if ovo and ovo != "UNREGISTERED":
            rows.append(f"• OVO: <b>{ovo}</b>")

        if dana and dana != "UNREGISTERED":
            rows.append(f"• Dana: <b>{dana}</b>")

        if linkaja and linkaja != "UNREGISTERED":
            rows.append(f"• LinkAja: <b>{linkaja}</b>")

        if shopeepay and shopeepay != "UNREGISTERED":
            rows.append(f"• ShopeePay: <b>{shopeepay}</b>")

        if isaku and isaku != "UNREGISTERED":
            rows.append(f"• iSaku: <b>{isaku}</b>")

        if rows:
            ewallet_text = (
                "\n\n💳 <b>E-Wallet</b>\n\n"
                + "\n".join(rows)
            )

    search_text = ""

    if search_engine:

        search_text = f"""

    🌐 <b>Search Engine</b>

    {html.escape(str(search_engine))}
    """

    # HEADER (TANPA INDENT)
    await msg_obj.edit_text(
        f"""📞 <b>Contact List</b>

    👤 <b>{html.escape(dominant.split('(')[0])}</b>
    <i>Primary Name</i>

    ━━━━━━━━━━━━━━

    {wa_block}
    {ewallet_text}
    {search_text}
    """,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    # TAG LIST
    lines = []

    for t, c in tags:
        name = format_display(t)

        if normalize_tag(t.lower()) == normalize_tag(dominant_name):
            line = f"⭐ <b>{html.escape(name)}</b> <code>({c})</code>"
        else:
            line = f"• {html.escape(name)} <code>({c})</code>"

        lines.append(line)

    MAX_LINES = 85
    chunks = [lines[i:i + MAX_LINES] for i in range(0, len(lines), MAX_LINES)]

    for i, chunk in enumerate(chunks):

        # 📌 hanya chat pertama
        if i == 0:
            text = "📌 <b>Tag List</b>\n\n"
        else:
            text = ""
            
        text += "\n".join(chunk)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="HTML"
        )

    # FOTO (PALING AKHIR)
    if gc_picture:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=gc_picture,
                caption=f"📇 <b>GetContact Profile</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            print("GC ERROR:", e)

    if wa_picture:
        try:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=wa_picture,
                caption=f"📱 <b>WhatsApp Profile</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            print("WA ERROR:", e)

    # QUOTA PALING AKHIR
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"⚡ <b>Remaining Quota:</b> <code>{quota}</code>",
        parse_mode="HTML"
    )
        
# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_quota(update.effective_user.id) == 0:
        set_quota(update.effective_user.id, 10)
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
