# app.py
import os
import time
import json
import threading
import requests
from flask import Flask, request

from telebot.types import ReplyKeyboardMarkup, KeyboardButton


# ================= MENU ====================
def main_menu():
    menu = ReplyKeyboardMarkup(resize_keyboard=True)
    menu.add(
        KeyboardButton("/start"), KeyboardButton("/help"),
        KeyboardButton("/save"), KeyboardButton("/list"),
        KeyboardButton("/delete"), KeyboardButton("/check"),
        KeyboardButton("/checkdie"), KeyboardButton("/layanh"),
        KeyboardButton("/info")
    )
    return menu


# =============== ENV ========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
FB_TOKEN = os.getenv("FB_TOKEN", "")
HOSTINGER_API_BASE = os.getenv("HOSTINGER_API_BASE", "").rstrip('/')
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
SECRET_KEY = os.getenv("SECRET_KEY", "")

from telebot import TeleBot, types
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)


# =============== HOSTINGER API ===============
def _params(x):
    if SECRET_KEY:
        x["key"] = SECRET_KEY
    return x

def h_get(chat):
    r = requests.get(HOSTINGER_API_BASE,
                     params=_params({"action": "get", "chat_id": chat}),
                     timeout=10)
    return r.json().get("data", {})

def h_save(chat, data):
    r = requests.post(HOSTINGER_API_BASE,
                      params=_params({"action": "save", "chat_id": chat}),
                      json=data, timeout=10)
    return r.status_code == 200

def h_update(chat, uid, payload):
    r = requests.post(HOSTINGER_API_BASE,
                      params=_params({"action": "update_uid", "chat_id": chat}),
                      json={"uid": uid, "payload": payload}, timeout=10)
    return r.status_code == 200

def h_delete(chat, uid):
    r = requests.post(HOSTINGER_API_BASE,
                      params=_params({"action": "delete", "chat_id": chat, "uid": uid}),
                      timeout=10)
    return r.status_code == 200

def h_ensure_chat(chat):
    cl = "_chat_list_"
    try:
        cur = h_get(cl)
        if not cur:
            cur = []
        if chat not in cur:
            cur.append(chat)
            h_save(cl, cur)
    except:
        pass


# ================= CHECK UID =================
def chk_avatar(uid):
    try:
        j = requests.get(
            f"https://graph.facebook.com/{uid}/picture?type=large&redirect=0&access_token={FB_TOKEN}",
            timeout=5).json()
        url = j.get("data", {}).get("url", "")
        if not url or "safe_image.php" in url or "scontent" not in url:
            return "DIE"
        return "LIVE"
    except:
        return "UNKNOWN"

def chk_cover(uid):
    try:
        j = requests.get(
            f"https://graph.facebook.com/{uid}?fields=cover&access_token={FB_TOKEN}",
            timeout=5).json()
        return "LIVE" if j.get("cover") else "DIE"
    except:
        return "UNKNOWN"

def chk_post(uid):
    try:
        r = requests.get(f"https://mbasic.facebook.com/{uid}",
                         headers={"User-Agent": "Mozilla"}, timeout=5)
        if any(x in r.text for x in ["Đã đăng", "Just now", "hours ago",
                                     "minutes ago", "giờ trước", "phút trước"]):
            return "LIVE"
        return "DIE"
    except:
        return "UNKNOWN"

def check_uid(uid):
    s = []
    if chk_avatar(uid) == "LIVE": s.append(1)
    if chk_cover(uid) == "LIVE": s.append(1)
    if chk_post(uid) == "LIVE": s.append(1)
    return "LIVE" if s else "DIE"


# ================= AUTO CHECK =================
def send_status(chat, uid, old, new):
    try:
        t = time.strftime("%d/%m %H:%M", time.gmtime(time.time() + 7 * 3600))
        bot.send_message(chat, f"🔔 UID: <b>{uid}</b>\n➡ Trạng thái: {old} ➜ <b>{new}</b>\n⏰ {t}")
    except:
        pass


def worker():
    while True:
        try:
            cl = h_get("_chat_list_") or [str(ADMIN_CHAT_ID)]
            for chat in cl:
                us = h_get(chat) or {}
                for uid, v in us.items():
                    new = check_uid(uid)
                    if new != v.get("status"):
                        old = v.get("status")
                        v["status"] = new
                        v["last_notified"] = int(time.time())
                        h_update(chat, uid, v)
                        send_status(chat, uid, old, new)
                    time.sleep(0.7)
        except:
            pass

        time.sleep(30)


# ================= WEBHOOK =================
@app.route('/webhook/' + TELEGRAM_TOKEN, methods=["POST"])
def wh():
    bot.process_new_updates([types.Update.de_json(request.json)])
    return "", 200


@app.route('/')
def home():
    return "BOT RUNNING ✅"


# ================= COMMANDS =================
flow = {}

@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id

    # ❌ người lạ dùng /start -> báo không được phép
    if str(chat_id) != str(ADMIN_CHAT_ID):
        return bot.send_message(
            chat_id,
            "❌ <b>Bạn không có quyền sử dụng BOT này!</b>\n"
            "Liên hệ Admin để được duyệt ✅",
            parse_mode="HTML"
        )

    # ✅ admin -> show menu
    bot.send_message(chat_id, "✅ BOT đã sẵn sàng!\nChọn chức năng:", reply_markup=main_menu())


@bot.message_handler(commands=['save'])
def save(m):
    flow[m.chat.id] = {"s": 1}
    bot.send_message(m.chat.id, "Gửi UID")

@bot.message_handler(func=lambda m: m.chat.id in flow)
def flow_handler(m):
    c = flow[m.chat.id]
    if c["s"] == 1:
        uid = m.text.strip()
        if not uid.isdigit():
            return bot.send_message(m.chat.id, "❌ UID không hợp lệ")
        c["uid"] = uid
        c["s"] = 2
        bot.send_message(m.chat.id, "Tên?")
    elif c["s"] == 2:
        c["name"] = m.text
        c["s"] = 3
        bot.send_message(m.chat.id, "Note?")
    else:
        uid, name, note = c["uid"], c["name"], m.text
        status = check_uid(uid)
        us = h_get(str(m.chat.id)) or {}
        us[uid] = {"name": name, "note": note, "status": status, "chat_id": str(m.chat.id)}
        h_save(str(m.chat.id), us)
        h_ensure_chat(str(m.chat.id))
        flow.pop(m.chat.id)
        bot.send_message(m.chat.id, f"✅ Đã lưu UID <b>{uid}</b> — {status}")


# ================= RUN =================
if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()

    # Set webhook theo URL thật của Render.com
    service_url = f"https://{os.getenv('RENDER_SERVICE_NAME')}.onrender.com"
    webhook_url = f"{service_url}/webhook/{TELEGRAM_TOKEN}"
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}")

    print("BOT RUNNING ✅")
    bot.remove_webhook()
    bot.set_webhook(url=webhook_url)

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

