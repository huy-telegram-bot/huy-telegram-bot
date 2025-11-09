# ================================================================
# TELEGRAM FB UID LIVE/DIE CHECK BOT (NO FB TOKEN)
# FULL VERSION FOR HOSTING/RENDER.COM + VPS
# Author: HUY (Custom build from VPS version)
# ================================================================

import os
import json
import time
import threading
import re
import requests
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ========================= CONFIG ==============================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
RENDER_SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "").strip()

DATA_FILE = "/tmp/uid_data.json"
CHECK_INTERVAL = 30  # auto check loop
REQUEST_TIMEOUT = 8

if TELEGRAM_TOKEN == "":
    raise RuntimeError("❌ Thiếu TELEGRAM_TOKEN trong env!")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0"})

# ========================= LOAD DATA ===========================
def load_data():
    try:
        if not os.path.exists(DATA_FILE):
            return {}
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(d):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except:
        pass

UID_LIST = load_data()
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ========================= FB CHECK ===========================
def check_avatar(uid):
    """
    Check avatar redirect:
    scontent => LIVE
    safe_image => DIE
    """
    try:
        url = f"https://www.facebook.com/{uid}/picture?type=large"
        r = session.get(url, allow_redirects=False, timeout=REQUEST_TIMEOUT)

        if "Location" in r.headers:
            loc = r.headers["Location"]
            if "scontent" in loc:
                return True
            if "safe_image" in loc:
                return False

        return False
    except:
        return False
FB_COOKIE = os.getenv("FB_COOKIE", "")  # cookie lấy từ trình duyệt FB

headers = {
    "User-Agent": "Mozilla/5.0",
    "Cookie": FB_COOKIE
}

def check_live(uid):
    try:
        # STEP 1: check avatar redirect (không follow)
        avatar = f"https://www.facebook.com/{uid}/picture?type=large"
        r = requests.get(avatar, headers=headers, allow_redirects=False, timeout=10)

        loc = r.headers.get("Location", "")

        if "scontent" in loc:
            return "LIVE"

        if "safe_image" in loc or "checkpoint" in loc:
            return "DIE"

        # STEP 2: check mbasic (phải có cookie FB mới xem được)
        mbasic = f"https://mbasic.facebook.com/{uid}"
        r2 = requests.get(mbasic, headers=headers, timeout=10).text.lower()

        keywords = ["đã đăng", "giờ trước", "phút trước", "just now", "minutes ago"]
        if any(k in r2 for k in keywords):
            return "LIVE"

        return "DIE"
    except:
        return "DIE"

def check_post(uid):
    """
    Check bài viết trên mbasic (giống VPS)
    """
    try:
        r = session.get(f"https://mbasic.facebook.com/{uid}", timeout=REQUEST_TIMEOUT)
        txt = r.text.lower()

        bad = ["bạn hiện không xem được nội dung này", "content not available", "page not found"]
        if any(k in txt for k in bad):
            return False

        live = ["đã đăng", "giờ trước", "phút trước", "just now", "minutes ago", "hours ago"]
        if any(k in txt for k in live):
            return True

        return False
    except:
        return False

def uid_status(uid):
    """
    LIVE nếu avatar hoặc bài viết hoạt động
    """
    if check_avatar(uid):
        return "LIVE"
    if check_post(uid):
        return "LIVE"
    return "DIE"

# ========================= MENU ===========================
def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("/save"), KeyboardButton("/list"),
        KeyboardButton("/check"), KeyboardButton("/checkdie"),
        KeyboardButton("/layanh"), KeyboardButton("/info"),
        KeyboardButton("/delete"), KeyboardButton("/deleteall")
    )
    return kb

# ========================= COMMANDS ===========================
user_step = {}

@bot.message_handler(commands=["start"])
def cmd_start(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return bot.send_message(m.chat.id, "⛔ Bạn không có quyền!")
    bot.send_message(m.chat.id, "✅ BOT đã sẵn sàng!", reply_markup=menu())

@bot.message_handler(commands=["help"])
def cmd_help(m):
    bot.send_message(m.chat.id,
"""
📌 Lệnh hỗ trợ:
/save - lưu UID
/list - xem danh sách
/delete <uid> - xóa UID
/deleteall - xóa tất cả
/check - check toàn bộ UID
/checkdie <uid> - check nhanh
/layanh <uid> - lấy avatar + cover
/info <uid> - thông tin FB (scrape)
""")

# SAVE FLOW
@bot.message_handler(commands=["save"])
def save1(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return bot.send_message(m.chat.id, "⛔ Không có quyền!")
    user_step[m.chat.id] = {"step": 1}
    bot.send_message(m.chat.id, "📥 Gửi UID hoặc link Facebook:")

@bot.message_handler(func=lambda m: m.chat.id in user_step)
def save2(m):
    step = user_step[m.chat.id]
    txt = m.text.strip()

    # STEP 1: Lấy UID
    if step["step"] == 1:
        if txt.isdigit():
            uid = txt
        else:
            match = re.search(r"(?:profile\.php\?id=)?([0-9]{6,})", txt)
            uid = match.group(1) if match else None

        if not uid:
            del user_step[m.chat.id]
            return bot.send_message(m.chat.id, "❌ Không tìm được UID!")

        step["uid"] = uid
        step["step"] = 2
        return bot.send_message(m.chat.id, "✏️ Tên hiển thị:")

    # STEP 2: Nhập tên
    if step["step"] == 2:
        step["name"] = txt
        step["step"] = 3
        return bot.send_message(m.chat.id, "📝 Ghi chú (note):")

    # STEP 3: Nhập note và lưu
    if step["step"] == 3:
        uid = step["uid"]
        name = step["name"]
        note = txt

        stt = uid_status(uid)

        UID_LIST[uid] = {
            "name": name, "note": note,
            "status": stt, "last_check": int(time.time())
        }
        save_data(UID_LIST)

        del user_step[m.chat.id]
        bot.send_message(m.chat.id, f"✅ Đã lưu UID <b>{uid}</b>\n📌 Trạng thái: <b>{stt}</b>")

# LIST UID
@bot.message_handler(commands=["list"])
def cmd_list(m):
    if not UID_LIST:
        return bot.send_message(m.chat.id,"⚠️ Không có UID nào.")
    msg = "📋 <b>Danh sách UID:</b>\n"
    for uid, d in UID_LIST.items():
        msg += f"• <b>{uid}</b> — {d['name']} ({d['status']})\n"
    bot.send_message(m.chat.id, msg)

# DELETE
@bot.message_handler(commands=["delete"])
def cmd_delete(m):
    sp = m.text.split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ /delete <uid>")
    uid = sp[1]
    if uid in UID_LIST:
        del UID_LIST[uid]
        save_data(UID_LIST)
        bot.send_message(m.chat.id, f"✅ Đã xóa {uid}")
    else:
        bot.send_message(m.chat.id,"❌ UID không tồn tại.")

@bot.message_handler(commands=["deleteall"])
def cmd_deleteall(m):
    UID_LIST.clear()
    save_data(UID_LIST)
    bot.send_message(m.chat.id,"🗑 XÓA TẤT CẢ THÀNH CÔNG.")

# CHECK
@bot.message_handler(commands=["check"])
def cmd_check(m):
    msg = "🔍 KẾT QUẢ CHECK:\n"
    for uid in UID_LIST:
        stt = uid_status(uid)
        UID_LIST[uid]["status"] = stt
        UID_LIST[uid]["last_check"] = int(time.time())
        msg += f"{uid} → <b>{stt}</b>\n"
        time.sleep(0.15)
    save_data(UID_LIST)
    bot.send_message(m.chat.id, msg)

@bot.message_handler(commands=["checkdie"])
def cmd_checkdie(m):
    uids = m.text.split()[1:]
    msg = ""
    for uid in uids:
        msg += f"{uid} → {uid_status(uid)}\n"
    bot.send_message(m.chat.id, msg)

# GET AVATAR + COVER
@bot.message_handler(commands=["layanh"])
def layanh(m):
    uid = m.text.split()[1]
    avatar = f"https://www.facebook.com/{uid}/picture?type=large"
    bot.send_message(m.chat.id, f"📷 Avatar:\n{avatar}")

# INFO (LẤY TÊN)
@bot.message_handler(commands=["info"])
def info(m):
    uid = m.text.split()[1]
    r = session.get(f"https://mbasic.facebook.com/{uid}", timeout=5)
    name = re.search(r"<title>(.*?)</title>", r.text)
    name = name.group(1) if name else "Không rõ"
    bot.send_message(m.chat.id, f"👤 Name: {name}\n🔗 https://facebook.com/{uid}")

# ======================= AUTO LOOP =======================
def auto_checker():
    while True:
        for uid in UID_LIST:
            old = UID_LIST[uid]["status"]
            new = uid_status(uid)

            UID_LIST[uid]["last_check"] = int(time.time())

            if new != old:
                UID_LIST[uid]["status"] = new
                save_data(UID_LIST)
                bot.send_message(ADMIN_CHAT_ID,
f"""
🔔 <b>THAY ĐỔI TRẠNG THÁI</b>
UID: <code>{uid}</code>
{old} ➝ <b>{new}</b>
""")

        time.sleep(CHECK_INTERVAL)

# ======================= WEBHOOK =========================
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = request.get_json()
    if update:
        bot.process_new_updates([types.Update.de_json(update)])
    return "OK", 200

def ensure_webhook():
    if RENDER_SERVICE_NAME:
        url = f"https://{RENDER_SERVICE_NAME}.onrender.com/webhook/{TELEGRAM_TOKEN}"
        requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={url}")
        print("✅ Webhook set:", url)

# ======================= MAIN ============================
if __name__ == "__main__":
    threading.Thread(target=auto_checker, daemon=True).start()
    ensure_webhook()
    app.run(host="0.0.0.0", port=PORT)
