# =========================================================
# ==========  TELEGRAM BOT CHECK UID LIVE / DIE  ==========
# ==========    By Huy — VPS accuracy version    ==========
# =========================================================

import os
import json
import re
import time
import random
import threading
import requests
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ================= CONFIG — BẮT BUỘC =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

# Render hosting: phải có tên service → auto set webhook
RENDER_SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()

PORT = int(os.getenv("PORT", "10000"))

# nơi lưu file JSON (Render cho phép /tmp)
DATA_FILE = "/tmp/uid_data.json"

# AUTO CHECK CONFIG
CHECK_INTERVAL = 30
REQUEST_TIMEOUT = 8

# ======================================================
if TELEGRAM_TOKEN == "":
    raise RuntimeError("❌ TELEGRAM_TOKEN không được để trống!")

session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124"})


# ================= DATA MANAGER ======================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_data(d):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


UID_LIST = load_data()


# ================== CHECK LIVE DIE (VPS accurate) ==================
def check_live_vps(uid):
    """ Check Facebook UID LIVE/DIE — giống VPS """

    try:
        # 1. Avatar redirect (nếu về CDN scontent → LIVE)
        r = session.get(
            f"https://www.facebook.com/{uid}/picture?type=large",
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

        loc = r.headers.get("Location", "")

        if ("scontent" in loc or "fbcdn" in loc) and "safe_image" not in loc:
            return "LIVE"

        # 2. Check mbasic (timeline/profile info)
        r2 = session.get(f"https://mbasic.facebook.com/{uid}", timeout=REQUEST_TIMEOUT)
        html = r2.text.lower()

        live_keywords = [
            "đã đăng", "giờ trước", "phút trước", "just now",
            "minutes ago", "hours ago", "chia sẻ", "shared"
        ]

        if any(k in html for k in live_keywords):
            return "LIVE"

        # nếu có avatar/cover loaded nghĩa là tài khoản vẫn tồn tại
        if "cover" in html or "add friend" in html or "followers" in html:
            return "LIVE"

        return "DIE"

    except:
        return "DIE"


# ================= TELEGRAM BOT SETUP ==================

bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)


def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("/save"), KeyboardButton("/check"),
        KeyboardButton("/list"), KeyboardButton("/checkdie"),
        KeyboardButton("/delete"), KeyboardButton("/layanh"),
        KeyboardButton("/info"), KeyboardButton("/deleteall")
    )
    return kb


# ============= COMMAND HANDLER ========================

@bot.message_handler(commands=["start"])
def cmd_start(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return bot.send_message(m.chat.id, "❌ Bạn không có quyền sử dụng BOT này.")

    bot.send_message(m.chat.id, "✅ BOT đã sẵn sàng!", reply_markup=main_menu())


@bot.message_handler(commands=["help"])
def cmd_help(m):
    help_msg = """
<b>📌 Hướng dẫn sử dụng:</b>
-----------------------------------
/save → Lưu UID
/list → Xem danh sách UID
/check → Check tất cả UID ngay
/checkdie <uid> → Check UID nhanh
/delete <uid> → Xóa UID
/deleteall → Xóa toàn bộ UID
/layanh <uid> → Lấy avatar + cover
/info <uid> → Lấy thông tin đơn giản
-----------------------------------
👑 Auto-check mỗi 30 giây
"""
    bot.send_message(m.chat.id, help_msg)


# ================= SAVE UID FLOW ==================

user_flow = {}  # lưu step nhập


@bot.message_handler(commands=["save"])
def cmd_save(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return bot.send_message(m.chat.id, "❌ Không có quyền!")

    user_flow[m.chat.id] = {"step": 1}
    bot.send_message(m.chat.id, "🔵 Gửi UID hoặc link Facebook:")


@bot.message_handler(func=lambda m: m.chat.id in user_flow)
def save_flow(m):
    chat = m.chat.id
    step = user_flow[chat]["step"]
    text = m.text.strip()

    if step == 1:
        uid = None

        if text.isdigit():
            uid = text
        else:
            match = re.search(r"([0-9]{6,})", text)
            if match:
                uid = match.group(1)

        if not uid:
            user_flow.pop(chat, None)
            return bot.send_message(chat, "❌ UID không hợp lệ. Gửi lại.")

        user_flow[chat]["uid"] = uid
        user_flow[chat]["step"] = 2
        return bot.send_message(chat, "✏ Nhập tên gợi nhớ:")

    elif step == 2:
        user_flow[chat]["name"] = text
        user_flow[chat]["step"] = 3
        return bot.send_message(chat, "🟣 Nhập ghi chú (note):")

    elif step == 3:
        uid = user_flow[chat]["uid"]
        name = user_flow[chat]["name"]
        note = text

        status = check_live_vps(uid)

        UID_LIST[uid] = {
            "name": name,
            "note": note,
            "status": status,
            "last_check": int(time.time())
        }

        save_data(UID_LIST)
        user_flow.pop(chat, None)

        return bot.send_message(chat, f"✅ Đã lưu UID <b>{uid}</b>\n🌍 Trạng thái: <b>{status}</b>")


# ================= LIST + DELETE ==================

@bot.message_handler(commands=["list"])
def cmd_list(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    if not UID_LIST:
        return bot.send_message(m.chat.id, "📭 Danh sách rỗng.")

    msg = "<b>📌 UID đã lưu:</b>\n"
    for uid, info in UID_LIST.items():
        msg += f"• <b>{uid}</b> - {info['name']} ({info['status']})\n"

    bot.send_message(m.chat.id, msg)


@bot.message_handler(commands=["delete"])
def cmd_delete(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    sp = m.text.split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /delete <uid>")

    uid = sp[1]
    if uid in UID_LIST:
        UID_LIST.pop(uid)
        save_data(UID_LIST)
        bot.send_message(m.chat.id, f"✅ Đã xóa UID: {uid}")
    else:
        bot.send_message(m.chat.id, "❌ UID không tồn tại!")


@bot.message_handler(commands=["deleteall"])
def cmd_delete_all(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    UID_LIST.clear()
    save_data(UID_LIST)
    bot.send_message(m.chat.id, "✅ Đã xóa toàn bộ UID")


# ================= MANUAL CHECK ==================

@bot.message_handler(commands=["check"])
def cmd_check_all(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    if not UID_LIST:
        return bot.send_message(m.chat.id, "📭 Không có UID nào.")

    msg = "<b>🔍 Kết quả check:</b>\n"
    for uid in UID_LIST:
        st = check_live_vps(uid)
        UID_LIST[uid]["status"] = st
        UID_LIST[uid]["last_check"] = int(time.time())
        msg += f"{uid} → <b>{st}</b>\n"
        time.sleep(0.15)

    save_data(UID_LIST)
    bot.send_message(m.chat.id, msg)


@bot.message_handler(commands=["checkdie"])
def cmd_checkdie(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    sp = m.text.split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /checkdie <uid>")

    out = "\n".join(f"{uid} → {check_live_vps(uid)}" for uid in sp[1:])
    bot.send_message(m.chat.id, out)


# ================= LẤY ẢNH (avatar + cover) ==================

@bot.message_handler(commands=["layanh"])
def cmd_layanh(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return

    sp = m.text.split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /layanh <uid>")

    uid = sp[1]

    avatar_url = f"https://www.facebook.com/{uid}/picture?type=large"
    bot.send_message(m.chat.id, f"📷 Avatar: {avatar_url}")

    try:
        r = session.get(f"https://mbasic.facebook.com/{uid}", timeout=REQUEST_TIMEOUT)
        m_cover = re.search(r'https?://[^"]*cover[^"]*', r.text)
        if m_cover:
            bot.send_message(m.chat.id, f"🖼 Cover: {m_cover.group(0)}")
    except:
        pass


# ================= AUTO CHECK BACKGROUND ==================

def auto_checker():
    while True:
        if UID_LIST:
            for uid, info in UID_LIST.items():
                old = info["status"]
                new = check_live_vps(uid)

                UID_LIST[uid]["last_check"] = int(time.time())

                if old != new:
                    UID_LIST[uid]["status"] = new
                    UID_LIST[uid]["last_change"] = int(time.time())
                    save_data(UID_LIST)

                    bot.send_message(
                        ADMIN_CHAT_ID,
                        f"🔔 UID thay đổi trạng thái!\n<b>{uid}</b>\n{old} → {new}"
                    )

                time.sleep(random.uniform(0.25, 0.6))  # tránh FB block

            save_data(UID_LIST)

        time.sleep(CHECK_INTERVAL)


# START BACKGROUND THREAD
threading.Thread(target=auto_checker, daemon=True).start()


# ================= WEBHOOK SETUP ==================

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json()
    bot.process_new_updates([types.Update.de_json(update)])
    return "OK", 200


def ensure_webhook():
    if WEBHOOK_URL:
        url = WEBHOOK_URL
    else:
        url = f"https://{RENDER_SERVICE_NAME}.onrender.com"

    full = f"{url}/webhook/{TELEGRAM_TOKEN}"
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={full}")
    print("✅ Webhook set:", full)


if __name__ == "__main__":
    ensure_webhook()
    print("🚀 Bot started — Webhook ACTIVE")
    app.run(host="0.0.0.0", port=PORT)
