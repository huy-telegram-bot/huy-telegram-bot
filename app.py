# ===============================================================
# ✅ Telegram Bot Auto Check Facebook UID (LIVE/DIE)
# ✅ Không dùng Facebook Graph API
# ✅ Check bằng avatar redirect + mbasic (giống VPS 100%)
# ✅ Auto check mỗi 30s, notify nếu UID đổi trạng thái
# ✅ Lưu UID vào JSON tại /tmp (Render hosting hỗ trợ)
# ===============================================================

import os
import json
import time
import threading
import re
import requests
from flask import Flask, request

from telebot import TeleBot, types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# -------------------- CONFIG --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
RENDER_SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

PORT = int(os.getenv("PORT", "10000"))
DATA_FILE = "/tmp/uid_data.json"

CHECK_INTERVAL = 30          # Auto check mỗi 30s
REQUEST_TIMEOUT = 8          # Timeout check UID
MIN_DELAY_PER_UID = 0.10     # Delay tối thiểu mỗi UID
MAX_DELAY_PER_UID = 0.5      # Delay tối đa mỗi UID

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN chưa được set trong Environment Variables.")

# -------------------- JSON STORAGE --------------------
def load_data():
    try:
        if not os.path.exists(DATA_FILE):
            return {}
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

UID_LIST = load_data()

# -------------------- TELEGRAM BOT --------------------
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("/save"), KeyboardButton("/list"),
        KeyboardButton("/check"), KeyboardButton("/checkdie"),
        KeyboardButton("/delete"), KeyboardButton("/deleteall"),
        KeyboardButton("/layanh"), KeyboardButton("/info")
    )
    return kb

# -------------------- CHECK UID (CHUẨN VPS) --------------------
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

def check_avatar(uid):
    try:
        r = session.get(
            f"https://www.facebook.com/{uid}/picture?type=large",
            allow_redirects=False, timeout=REQUEST_TIMEOUT
        )
        loc = r.headers.get("Location", "")

        if "scontent" in loc or "cdn" in loc:
            return True  # LIVE avatar

        return False
    except:
        return False


def check_post(uid):
    try:
        r = session.get(f"https://mbasic.facebook.com/{uid}", timeout=REQUEST_TIMEOUT)
        html = r.text.lower()

        keywords = ["vừa xong", "giờ trước", "phút trước", "just now", "minutes ago", "hours ago"]

        return any(k in html for k in keywords)
    except:
        return False


def check_live_vps(uid):
    """ ✅ Nếu avatar LIVE hoặc có bài viết → LIVE """
    if check_avatar(uid) or check_post(uid):
        return "LIVE"
    return "DIE"


# -------------------- AUTO CHECK BACKGROUND --------------------
def notify_change(uid, old, new, name):
    bot.send_message(
        ADMIN_CHAT_ID,
        f"🔔 <b>UID thay đổi trạng thái</b>\n"
        f"👤 UID: <code>{uid}</code>\n"
        f"📝 Tên: {name}\n"
        f"🔁 <b>{old} → {new}</b>"
    )


def auto_checker():
    while True:
        data = load_data()
        if not data:
            time.sleep(10)
            continue

        delay = max(MIN_DELAY_PER_UID, min(MAX_DELAY_PER_UID, CHECK_INTERVAL / len(data)))

        for uid, meta in list(data.items()):
            new = check_live_vps(uid)
            old = meta.get("status", "DIE")

            meta["last_check"] = int(time.time())

            if new != old:
                meta["status"] = new
                save_data(data)
                notify_change(uid, old, new, meta.get("name", "-"))

            time.sleep(delay)

        save_data(data)


# -------------------- COMMAND HANDLERS --------------------
user_flow = {}

@bot.message_handler(commands=['start'])
def start(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return bot.send_message(m.chat.id, "❌ Không có quyền sử dụng bot này.")
    bot.send_message(m.chat.id, "✅ BOT đã sẵn sàng!", reply_markup=menu())


@bot.message_handler(commands=['save'])
def save(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    user_flow[m.chat.id] = {"step": 1}
    bot.send_message(m.chat.id, "🔵 Gửi UID hoặc link Facebook:")


@bot.message_handler(func=lambda m: m.chat.id in user_flow)
def save_flow(m):
    flow = user_flow[m.chat.id]
    step = flow["step"]

    if step == 1:
        t = m.text.strip()
        uid = None

        if t.isdigit():
            uid = t
        else:
            match = re.search(r"(?:profile\.php\?id=)?([0-9]{6,})", t)
            if match:
                uid = match.group(1)

        if not uid:
            user_flow.pop(m.chat.id)
            return bot.send_message(m.chat.id, "❌ UID không hợp lệ, thử lại.")

        flow["uid"] = uid
        flow["step"] = 2
        bot.send_message(m.chat.id, "🔵 Nhập tên hiển thị:")
        return

    if step == 2:
        flow["name"] = m.text
        flow["step"] = 3
        return bot.send_message(m.chat.id, "🔵 Nhập ghi chú (note):")

    if step == 3:
        uid = flow["uid"]
        name = flow["name"]
        note = m.text

        status = check_live_vps(uid)

        UID_LIST[uid] = {
            "name": name,
            "note": note,
            "status": status,
            "last_check": int(time.time())
        }
        save_data(UID_LIST)
        user_flow.pop(m.chat.id)

        bot.send_message(m.chat.id, f"✅ Lưu UID <b>{uid}</b> — Trạng thái: <b>{status}</b>")


@bot.message_handler(commands=['list'])
def list_uid(m):
    if m.chat.id != ADMIN_CHAT_ID: return

    if not UID_LIST:
        return bot.send_message(m.chat.id, "⚠️ Chưa có UID nào.")

    msg = "<b>📋 DANH SÁCH UID:</b>\n"
    for uid, meta in UID_LIST.items():
        msg += f"• <b>{uid}</b> — {meta['name']} — {meta['status']}\n"

    bot.send_message(m.chat.id, msg)


@bot.message_handler(commands=['delete'])
def delete_uid(m):
    if m.chat.id != ADMIN_CHAT_ID: return
    uid = m.text.replace("/delete", "").strip()

    if uid in UID_LIST:
        del UID_LIST[uid]
        save_data(UID_LIST)
        bot.send_message(m.chat.id, f"✅ Đã xóa UID {uid}")
    else:
        bot.send_message(m.chat.id, "❌ UID không tồn tại")


@bot.message_handler(commands=['deleteall'])
def delete_all(m):
    if m.chat.id != ADMIN_CHAT_ID: return
    UID_LIST.clear()
    save_data({})
    bot.send_message(m.chat.id, "✅ Đã xóa toàn bộ UID")


@bot.message_handler(commands=['check'])
def check_all(m):
    if m.chat.id != ADMIN_CHAT_ID: return

    msg = "🔍 <b>KẾT QUẢ CHECK:</b>\n"

    for uid in UID_LIST:
        st = check_live_vps(uid)
        UID_LIST[uid]["status"] = st
        msg += f"{uid} → <b>{st}</b>\n"
        time.sleep(0.1)

    save_data(UID_LIST)
    bot.send_message(m.chat.id, msg)


@bot.message_handler(commands=['checkdie'])
def check_die(m):
    if m.chat.id != ADMIN_CHAT_ID: return

    sp = m.text.split()
    msg = ""

    for uid in sp[1:]:
        msg += f"{uid} → {check_live_vps(uid)}\n"

    bot.send_message(m.chat.id, msg)


@bot.message_handler(commands=['layanh'])
def layanh(m):
    if m.chat.id != ADMIN_CHAT_ID: return
    uid = m.text.split()[1]

    r = session.get(
        f"https://www.facebook.com/{uid}/picture?type=large",
        allow_redirects=False,
        timeout=REQUEST_TIMEOUT
    )
    loc = r.headers.get("Location", "")
    bot.send_message(m.chat.id, f"📷 {loc}")


@bot.message_handler(commands=['info'])
def info(m):
    if m.chat.id != ADMIN_CHAT_ID: return
    uid = m.text.split()[1]
    bot.send_message(m.chat.id, f"https://facebook.com/{uid}")


# -------------------- WEBHOOK --------------------
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    bot.process_new_updates([types.Update.de_json(request.get_json())])
    return "OK", 200


def ensure_webhook():
    url = (
        WEBHOOK_URL.rstrip("/")
        if WEBHOOK_URL
        else f"https://{RENDER_SERVICE_NAME}.onrender.com"
    ) + f"/webhook/{TELEGRAM_TOKEN}"

    cur = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo").json()
    if cur.get("result", {}).get("url") != url:
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={url}")


# -------------------- RUN --------------------
if __name__ == "__main__":
    print("🚀 BOT STARTED — AUTO CHECK EVERY 30s")
    threading.Thread(target=auto_checker, daemon=True).start()
    ensure_webhook()
    app.run(host="0.0.0.0", port=PORT)
