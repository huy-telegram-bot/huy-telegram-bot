# app.py
# Telegram bot LIVE/DIE — hoạt động như bản VPS
# Không dùng Graph API, không cần token Facebook
# Check bằng: mbasic + content unavailable + checkpoint
# Auto check mỗi 30s / hỗ trợ 500 UID

import os
import json
import time
import threading
import requests
import re
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ========================
# CONFIG
# ========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
PORT = int(os.getenv("PORT", "10000"))

DATA_FILE = "/tmp/uid_data.json"
CHECK_INTERVAL = 30
REQUEST_TIMEOUT = 8

if not TELEGRAM_TOKEN:
    raise RuntimeError("⚠️ TELEGRAM_TOKEN chưa cài trong ENV")

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
})

# ========================
# Data
# ========================
def load_data():
    try:
        if not os.path.exists(DATA_FILE):
            return {}
        return json.load(open(DATA_FILE, "r", encoding="utf-8"))
    except:
        return {}

def save_data(data):
    try:
        json.dump(data, open(DATA_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except:
        pass

UID_LIST = load_data()

# ========================
# LIVE / DIE CHECK (LIKE VPS)
# ========================
def check_live(uid):
    try:
        url = f"https://mbasic.facebook.com/{uid}"
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        html = r.text.lower()

        # ✅ UID chết / khóa / checkpoint / delete
        die_keywords = [
            "content isn't available",
            "không khả dụng",
            "tài khoản hiện không khả dụng",
            "account disabled",
            "unavailable",
            "checkpoint",
            "not found"
        ]
        if any(k in html for k in die_keywords):
            return "DIE"

        # ✅ Có profile (LIVE)
        live_patterns = [
            "add friend", "kết bạn",
            "follow", "theo dõi",
            "intro", "giới thiệu",
            "friends", "bạn bè"
        ]
        if any(k in html for k in live_patterns):
            return "LIVE"

        # ✅ Fallback
        return "LIVE"

    except:
        return "DIE"

# ========================
# Worker Background check
# ========================
def auto_check():
    global UID_LIST
    while True:
        data = load_data()
        for uid, meta in data.items():
            old = meta.get("status", "DIE")
            new = check_live(uid)

            if old != new:
                data[uid]["status"] = new
                data[uid]["last_change"] = int(time.time())
                save_data(data)

                # gửi thông báo khi đổi trạng thái
                try:
                    bot.send_message(
                        ADMIN_CHAT_ID,
                        f"🔔 UID thay đổi trạng thái\n<code>{uid}</code>\n<b>{old} → {new}</b>",
                        parse_mode="HTML"
                    )
                except:
                    pass

            time.sleep(0.3)

        UID_LIST = data
        time.sleep(CHECK_INTERVAL)

# ========================
# Telegram bot
# ========================
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

def menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("/save", "/list", "/delete", "/deleteall", "/check")
    return kb

@bot.message_handler(commands=["start"])
def start(m):
    if m.chat.id != ADMIN_CHAT_ID:
        return bot.send_message(m.chat.id, "🚫 Bạn không có quyền dùng bot")
    bot.send_message(m.chat.id, "✅ Bot hoạt động!", reply_markup=menu())

@bot.message_handler(commands=["save"])
def save(m):
    bot.reply_to(m, "📌 Gửi UID hoặc link Facebook:")
    bot.register_next_step_handler(m, step_uid)

def step_uid(m):
    text = m.text.strip()
    uid = re.findall(r"\d{6,}", text)
    if not uid:
        return bot.reply_to(m, "❌ UID không hợp lệ, nhập lại `/save`")
    uid = uid[0]

    bot.reply_to(m, f"📌 Tên hiển thị cho UID {uid}:")
    bot.register_next_step_handler(m, step_name, uid)

def step_name(m, uid):
    name = m.text.strip()
    bot.reply_to(m, "📌 Ghi chú UID:")
    bot.register_next_step_handler(m, step_note, uid, name)

def step_note(m, uid, name):
    note = m.text.strip()
    status = check_live(uid)

    UID_LIST[uid] = {
        "name": name,
        "note": note,
        "status": status,
        "last_check": int(time.time())
    }
    save_data(UID_LIST)

    bot.reply_to(m, f"✅ Lưu UID <b>{uid}</b>\n📌 Trạng thái: <b>{status}</b>")

@bot.message_handler(commands=["list"])
def list_uid(m):
    data = load_data()
    if not data:
        return bot.reply_to(m, "⚠️ Chưa có UID nào")

    msg = "📋 <b>Danh sách UID:</b>\n"
    for uid, meta in data.items():
        msg += f"• {uid} → {meta.get('status')}\n"

    bot.send_message(m.chat.id, msg)

@bot.message_handler(commands=["delete"])
def delete_uid(m):
    uid = m.text.replace("/delete", "").strip()
    if uid in UID_LIST:
        UID_LIST.pop(uid)
        save_data(UID_LIST)
        bot.reply_to(m, f"✅ Xoá UID {uid}")
    else:
        bot.reply_to(m, "❌ UID không tồn tại")

@bot.message_handler(commands=["deleteall"])
def delete_all(m):
    save_data({})
    bot.reply_to(m, "✅ Đã xoá toàn bộ UID")

@bot.message_handler(commands=["check"])
def manual_check(m):
    data = load_data()
    msg = ""
    for uid in data:
        st = check_live(uid)
        msg += f"{uid} → {st}\n"
        time.sleep(0.3)
    bot.reply_to(m, msg or "⚠️ Không có UID nào")

# ========================
# Webhook
# ========================
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    bot.process_new_updates([types.Update.de_json(request.get_json(force=True))])
    return "OK"

# ========================
# Main
# ========================
if __name__ == "__main__":
    threading.Thread(target=auto_check, daemon=True).start()
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL + "/" + TELEGRAM_TOKEN)
    app.run(host="0.0.0.0", port=PORT)
