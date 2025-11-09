# app.py
# Full Telegram bot (Webhook) + auto-check worker
# Features:
# - /start, /help, /save, /list, /delete, /deleteall, /check, /checkdie, /layanh, /info, auto-check
# - Works with Hostinger JSON API if provided (HOSTINGER_API_BASE), otherwise falls back to local file storage (demo)
# - Designed to run on Render.com (webhook) but also usable elsewhere
# Requirements: pyTelegramBotAPI, Flask, requests
# Install: pip install pyTelegramBotAPI Flask requests

import os
import time
import json
import threading
import re
import requests
from flask import Flask, request, abort

from telebot import TeleBot, types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# ----------------- Configuration / ENV -----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
FB_TOKEN = os.getenv("FB_TOKEN", "").strip()  # optional
HOSTINGER_API_BASE = os.getenv("HOSTINGER_API_BASE", "").rstrip('/')  # optional
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()  # optional for Hostinger API
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))  # seconds for auto-check
# If running on Render, set RENDER_SERVICE_NAME to the app name (used to derive domain). Alternatively set WEBHOOK_URL env manually.
RENDER_SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "").strip()
WEBHOOK_URL_OVERRIDE = os.getenv("WEBHOOK_URL", "").strip()  # optional full url

# Basic validation
if not TELEGRAM_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN environment variable")

# ----------------- Bot & Flask -----------------
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# ----------------- Helpers: storage (Hostinger API or local file fallback) -----------------
# Local data directory (demo)
LOCAL_DATA_DIR = "/tmp/tgbot_data"
os.makedirs(LOCAL_DATA_DIR, exist_ok=True)

def _hostinger_params(params):
    if SECRET_KEY:
        params["key"] = SECRET_KEY
    return params

def _local_users_file(chat_id):
    return os.path.join(LOCAL_DATA_DIR, f"users_{chat_id}.json")

def _local_read(chat_id):
    fn = _local_users_file(chat_id)
    if not os.path.exists(fn):
        return {}
    try:
        return json.load(open(fn, "r", encoding="utf-8"))
    except:
        return {}

def _local_write(chat_id, data):
    fn = _local_users_file(chat_id)
    json.dump(data, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# Public functions used by bot/workers
def get_users(chat_id):
    """Return dict uid -> metadata"""
    chat_id = str(chat_id)
    if HOSTINGER_API_BASE:
        try:
            r = requests.get(HOSTINGER_API_BASE, params=_hostinger_params({"action":"get", "chat_id":chat_id}), timeout=10)
            j = r.json()
            return j.get("data", {}) if isinstance(j, dict) else {}
        except Exception:
            return {}
    else:
        return _local_read(chat_id)

def save_users(chat_id, users_dict):
    chat_id = str(chat_id)
    if HOSTINGER_API_BASE:
        try:
            r = requests.post(HOSTINGER_API_BASE, params=_hostinger_params({"action":"save", "chat_id":chat_id}), json=users_dict, timeout=10)
            return r.status_code == 200
        except Exception:
            return False
    else:
        _local_write(chat_id, users_dict)
        return True

def update_user(chat_id, uid, payload):
    chat_id = str(chat_id)
    if HOSTINGER_API_BASE:
        try:
            r = requests.post(HOSTINGER_API_BASE, params=_hostinger_params({"action":"update_uid", "chat_id":chat_id}), json={"uid":uid,"payload":payload}, timeout=10)
            return r.status_code == 200
        except Exception:
            return False
    else:
        users = _local_read(chat_id)
        users[str(uid)] = payload
        _local_write(chat_id, users)
        return True

def delete_user(chat_id, uid):
    chat_id = str(chat_id)
    if HOSTINGER_API_BASE:
        try:
            r = requests.post(HOSTINGER_API_BASE, params=_hostinger_params({"action":"delete", "chat_id":chat_id, "uid":str(uid)}), timeout=10)
            return r.status_code == 200
        except Exception:
            return False
    else:
        users = _local_read(chat_id)
        if str(uid) in users:
            users.pop(str(uid), None)
            _local_write(chat_id, users)
        return True

def delete_all_users(chat_id):
    chat_id = str(chat_id)
    if HOSTINGER_API_BASE:
        try:
            r = requests.post(HOSTINGER_API_BASE, params=_hostinger_params({"action":"delete_all", "chat_id":chat_id}), timeout=10)
            return r.status_code == 200
        except Exception:
            return False
    else:
        _local_write(chat_id, {})
        return True

# Chat list management (for worker)
CHAT_LIST_KEY = "_chat_list_"
def get_chat_list():
    if HOSTINGER_API_BASE:
        try:
            r = requests.get(HOSTINGER_API_BASE, params=_hostinger_params({"action":"get", "chat_id":CHAT_LIST_KEY}), timeout=10)
            j = r.json()
            data = j.get("data", {})
            # Accept list or dict
            if isinstance(data, list):
                return [str(x) for x in data]
            if isinstance(data, dict):
                return list(data.keys())
            return []
        except:
            return []
    else:
        fn = os.path.join(LOCAL_DATA_DIR, "chat_list.json")
        if not os.path.exists(fn):
            return []
        try:
            return [str(x) for x in json.load(open(fn, "r", encoding="utf-8"))]
        except:
            return []

def add_chat_to_list(chat_id):
    chat_id = str(chat_id)
    if HOSTINGER_API_BASE:
        try:
            cur = get_chat_list()
            if chat_id not in cur:
                cur.append(chat_id)
                requests.post(HOSTINGER_API_BASE, params=_hostinger_params({"action":"save", "chat_id":CHAT_LIST_KEY}), json=cur, timeout=10)
        except:
            pass
    else:
        fn = os.path.join(LOCAL_DATA_DIR, "chat_list.json")
        cur = get_chat_list()
        if chat_id not in cur:
            cur.append(chat_id)
            json.dump(cur, open(fn, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ----------------- Facebook check utilities -----------------
def chk_avatar(uid):
    """Check via Graph API picture redirect=0"""
    if not FB_TOKEN:
        return "UNKNOWN"
    try:
        url = f"https://graph.facebook.com/{uid}/picture?type=large&redirect=0&access_token={FB_TOKEN}"
        j = requests.get(url, timeout=8).json()
        pic = j.get("data",{}).get("url","")
        if not pic or "safe_image.php" in pic or "scontent" not in pic:
            return "DIE"
        return "LIVE"
    except:
        return "UNKNOWN"

def chk_cover(uid):
    if not FB_TOKEN:
        return "UNKNOWN"
    try:
        url = f"https://graph.facebook.com/{uid}?fields=cover&access_token={FB_TOKEN}"
        j = requests.get(url, timeout=8).json()
        if j.get("error"):
            return "UNKNOWN"
        return "LIVE" if j.get("cover") else "DIE"
    except:
        return "UNKNOWN"

def chk_post(uid):
    # Scrape mbasic for recent posts wording
    try:
        r = requests.get(f"https://mbasic.facebook.com/{uid}", headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        txt = r.text
        if any(k in txt for k in ["Đã đăng", "Just now", "hours ago", "minutes ago", "giờ trước", "phút trước"]):
            return "LIVE"
        return "DIE"
    except:
        return "UNKNOWN"

def check_uid(uid):
    """Combine signals: if any live signal => LIVE, else DIE; UNKNOWN if uncertain"""
    s = []
    a = chk_avatar(uid)
    if a == "UNKNOWN":
        # if avatar unknown, continue but prefer other signals
        pass
    if a == "LIVE": s.append("avatar")
    c = chk_cover(uid)
    if c == "LIVE": s.append("cover")
    p = chk_post(uid)
    if p == "LIVE": s.append("post")
    if s:
        return "LIVE"
    # If all UNKNOWN, return UNKNOWN, else DIE
    if a == "UNKNOWN" and c == "UNKNOWN" and p == "UNKNOWN":
        return "UNKNOWN"
    return "DIE"

# ----------------- Utils: fetch avatar & cover images -----------------
def get_avatar_and_cover(uid):
    avatar_url = ""
    cover_url = ""
    try:
        # avatar (redirect=0)
        r = requests.get(f"https://graph.facebook.com/{uid}/picture?type=large&redirect=0&access_token={FB_TOKEN}", timeout=8).json()
        avatar_url = r.get("data",{}).get("url","")
    except:
        pass
    try:
        r = requests.get(f"https://graph.facebook.com/{uid}?fields=cover&access_token={FB_TOKEN}", timeout=8).json()
        cover = r.get("cover")
        if cover:
            cover_url = cover.get("source","")
    except:
        pass
    return avatar_url, cover_url

# ----------------- UI: main menu keyboard -----------------
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(
        KeyboardButton("/start"), KeyboardButton("/help"),
        KeyboardButton("/save"), KeyboardButton("/list"),
        KeyboardButton("/delete"), KeyboardButton("/check"),
        KeyboardButton("/checkdie"), KeyboardButton("/layanh"),
        KeyboardButton("/info")
    )
    return markup

# ----------------- Worker: periodic checking -----------------
def send_status_change(chat_id, uid, old_status, new_status, meta):
    try:
        t = time.strftime("%d/%m %H:%M", time.gmtime(time.time()+7*3600))
        text = (f"🔔 <b>UID</b>: <a href='https://facebook.com/{uid}'>{uid}</a>\n"
                f"🔁 Trạng thái: {old_status} ➜ <b>{new_status}</b>\n"
                f"👤 Tên: {meta.get('name','-')}\n"
                f"🕰️ {t}")
        bot.send_message(int(chat_id), text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass

def worker_loop():
    print("[WORKER] Starting worker loop, interval", CHECK_INTERVAL)
    while True:
        try:
            chats = get_chat_list() or ([str(ADMIN_CHAT_ID)] if ADMIN_CHAT_ID else [])
            for chat in chats:
                users = get_users(chat) or {}
                # users: uid -> metadata
                for uid, meta in list(users.items()):
                    prev = meta.get("status")
                    new = check_uid(uid)
                    if new != prev:
                        meta["status"] = new
                        meta["last_checked"] = int(time.time())
                        update_user(chat, uid, meta)
                        send_status_change(chat, uid, prev, new, meta)
                    time.sleep(0.6)
        except Exception as e:
            print("Worker exception:", e)
        time.sleep(CHECK_INTERVAL)

# ----------------- Webhook endpoint -----------------
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    if request.headers.get('content-type') != 'application/json' and request.headers.get('Content-Type','').lower() != 'application/json':
        # Telegram sends application/json
        pass
    update = request.get_json(force=True)
    try:
        # Let pyTelegramBotAPI process the update
        bot.process_new_updates([types.Update.de_json(update)])
    except Exception as e:
        print("process update err:", e)
    return "", 200

@app.route("/")
def index():
    return "Bot is running"

# ----------------- Command Handlers -----------------
user_flow = {}  # chat_id -> state dict

@bot.message_handler(commands=['start'])
def cmd_start(m):
    chat_id = m.chat.id
    # Authorization: only ADMIN_CHAT_ID can use; others get message
    if ADMIN_CHAT_ID and str(chat_id) != str(ADMIN_CHAT_ID):
        bot.send_message(chat_id, "❌ <b>Bạn không có quyền sử dụng BOT này!</b>\nLiên hệ admin để được duyệt.", parse_mode="HTML")
        return
    add_chat_to_list(chat_id)
    bot.send_message(chat_id, "✅ BOT đã sẵn sàng!\nChọn chức năng:", reply_markup=main_menu())

@bot.message_handler(commands=['help'])
def cmd_help(m):
    text = ("<b>Menu hỗ trợ:</b>\n"
            "/start - Khởi động\n"
            "/save - Lưu UID\n"
            "/list - Danh sách UID\n"
            "/delete UID - Xóa 1 UID\n"
            "/deleteall - Xóa toàn bộ\n"
            "/check - Check tất cả UID\n"
            "/checkdie UID1 UID2 ... - Check nhanh\n"
            "/layanh UID - Lấy ảnh đại diện + bìa\n"
            "/info UID - Thông tin chi tiết")
    bot.send_message(m.chat.id, text, parse_mode="HTML")

@bot.message_handler(commands=['save'])
def cmd_save(m):
    chat_id = m.chat.id
    add_chat_to_list(chat_id)
    user_flow[chat_id] = {"step":"await_uid"}
    bot.send_message(chat_id, "🔵 Vui lòng gửi UID (số) hoặc link Facebook:")

@bot.message_handler(func=lambda m: m.chat.id in user_flow)
def flow_handler(m):
    chat_id = m.chat.id
    state = user_flow.get(chat_id, {})
    text = (m.text or "").strip()
    if state.get("step") == "await_uid":
        # try to extract numeric id
        uid = None
        # common profile.php?id=123 style
        m1 = re.search(r"(?:profile\.php\?id=)?([0-9]{6,})", text)
        if m1:
            uid = m1.group(1)
        else:
            # pure digits?
            if text.isdigit():
                uid = text
        if not uid:
            bot.send_message(chat_id, "❌ UID không hợp lệ. Gửi lại UID số hoặc link chứa id.")
            user_flow.pop(chat_id, None)
            return
        state["uid"] = uid
        state["step"] = "await_name"
        bot.send_message(chat_id, "🔵 Nhập Tên hiển thị cho UID:")
    elif state.get("step") == "await_name":
        state["name"] = text or "Không rõ"
        state["step"] = "await_note"
        bot.send_message(chat_id, "🔵 Nhập ghi chú (note) cho UID:")
    elif state.get("step") == "await_note":
        note = text or ""
        uid = state.get("uid")
        name = state.get("name")
        status = check_uid(uid)
        users = get_users(chat_id) or {}
        users[str(uid)] = {
            "name": name,
            "note": note,
            "status": status,
            "added_at": time.strftime("%d/%m/%Y %H:%M:%S", time.gmtime(time.time()+7*3600))
        }
        save_users(chat_id, users)
        add_chat_to_list(chat_id)
        bot.send_message(chat_id, f"✅ Đã lưu UID <b>{uid}</b> — Trạng thái: {status}", parse_mode="HTML")
        user_flow.pop(chat_id, None)
    else:
        user_flow.pop(chat_id, None)
        bot.send_message(chat_id, "Đã hủy flow.")

@bot.message_handler(commands=['list'])
def cmd_list(m):
    chat_id = m.chat.id
    users = get_users(chat_id) or {}
    if not users:
        bot.send_message(chat_id, "⚠️ Danh sách rỗng.")
        return
    lines = []
    for uid, data in users.items():
        lines.append(f"👤 <b>{data.get('name','-')}</b>\n🆔 <a href='https://facebook.com/{uid}'>{uid}</a>\n📌 {data.get('note','')}\nTrạng thái: {data.get('status','-')}\n")
    bot.send_message(chat_id, "<b>📋 Danh sách UID:</b>\n\n" + "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

@bot.message_handler(commands=['delete'])
def cmd_delete(m):
    chat_id = m.chat.id
    args = m.text.split()
    if len(args) < 2:
        bot.send_message(chat_id, "Sử dụng: /delete UID")
        return
    uid = args[1].strip()
    delete_user(chat_id, uid)
    bot.send_message(chat_id, f"✅ Đã xóa UID {uid}")

@bot.message_handler(commands=['deleteall'])
def cmd_deleteall(m):
    chat_id = m.chat.id
    delete_all_users(chat_id)
    bot.send_message(chat_id, "✅ Đã xóa toàn bộ UID.")

@bot.message_handler(commands=['check'])
def cmd_check(m):
    chat_id = m.chat.id
    users = get_users(chat_id) or {}
    if not users:
        bot.send_message(chat_id, "Danh sách rỗng.")
        return
    res = []
    for uid, meta in users.items():
        st = check_uid(uid)
        meta["status"] = st
        update_user(chat_id, uid, meta)
        res.append(f"{uid} → {st}")
        time.sleep(0.6)
    bot.send_message(chat_id, "Kết quả:\n" + "\n".join(res))

@bot.message_handler(commands=['checkdie'])
def cmd_checkdie(m):
    chat_id = m.chat.id
    args = m.text.replace("/checkdie","").strip().split()
    if not args:
        bot.send_message(chat_id, "Sử dụng: /checkdie UID1 UID2 ...")
        return
    out = []
    for uid in args:
        st = check_uid(uid)
        out.append(f"{uid} → {st}")
        time.sleep(0.6)
    bot.send_message(chat_id, "\n".join(out))

@bot.message_handler(commands=['layanh'])
def cmd_layanh(m):
    chat_id = m.chat.id
    args = m.text.replace("/layanh","").strip().split()
    if not args:
        bot.send_message(chat_id, "Gõ /layanh UID")
        return
    uid = args[0].strip()
    avatar, cover = get_avatar_and_cover(uid)
    text = f"🖼️ Ảnh của {uid}"
    if avatar:
        bot.send_message(chat_id, f"Avatar:\n{avatar}")
    else:
        bot.send_message(chat_id, "Không lấy được avatar.")
    if cover:
        bot.send_message(chat_id, f"Cover:\n{cover}")

@bot.message_handler(commands=['info'])
def cmd_info(m):
    chat_id = m.chat.id
    args = m.text.replace("/info","").strip()
    if not args or not args.isdigit():
        bot.send_message(chat_id, "Sử dụng: /info UID")
        return
    uid = args
    if not FB_TOKEN:
        bot.send_message(chat_id, "Cần FB_TOKEN để truy vấn thông tin.")
        return
    try:
        fields = "id,name,link,username,cover,locale,updated_time"
        r = requests.get(f"https://graph.facebook.com/{uid}", params={"fields":fields, "access_token":FB_TOKEN}, timeout=10).json()
        if r.get("error"):
            bot.send_message(chat_id, f"❌ Lỗi Facebook API: {r['error'].get('message')}")
            return
        out = (f"👤 {r.get('name','-')}\nID: {r.get('id','-')}\nLink: {r.get('link','-')}\nLocale: {r.get('locale','-')}\nUpdated: {r.get('updated_time','-')}")
        bot.send_message(chat_id, out)
    except Exception as e:
        bot.send_message(chat_id, "Lỗi khi gọi Facebook API.")

# Auto-extract UID from messages containing facebook links
@bot.message_handler(func=lambda m: m.text and ("facebook.com" in m.text or "fb.com" in m.text))
def msg_getuid(m):
    txt = m.text.strip()
    # extract url
    murl = re.search(r"https?://[^\s]+", txt)
    if not murl:
        return
    url = murl.group(0)
    # try numeric id first
    m1 = re.search(r"(?:profile\.php\?id=)?([0-9]{6,})", url)
    if m1:
        uid = m1.group(1)
        bot.send_message(m.chat.id, f"✅ UID: <code>{uid}</code>", parse_mode="HTML")
        return
    # fallback: try public id via simple external service
    try:
        r = requests.post("https://id.traodoisub.com/api.php", data={"link":url}, timeout=8)
        j = r.json()
        if j.get("success") == 200 and j.get("id"):
            bot.send_message(m.chat.id, f"✅ UID: <code>{j['id']}</code>", parse_mode="HTML")
            return
    except:
        pass
    bot.send_message(m.chat.id, "❌ Không thể lấy UID từ link này.")

# ----------------- Startup / Webhook setup -----------------
def ensure_webhook_set():
    """Set webhook only if needed to avoid Telegram 429"""
    # Determine expected webhook URL
    if WEBHOOK_URL_OVERRIDE:
        service_url = WEBHOOK_URL_OVERRIDE.rstrip('/')
    elif RENDER_SERVICE_NAME:
        service_url = f"https://{RENDER_SERVICE_NAME}.onrender.com"
    else:
        # Try to discover (Render provides RENDER_EXTERNAL_URL sometimes)
        service_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("WEBHOOK_URL") or ""
        service_url = service_url.rstrip('/')
    if not service_url:
        print("[WARN] No service URL known; skipping automatic webhook set. You must set webhook manually.")
        return
    webhook_url = f"{service_url}/webhook/{TELEGRAM_TOKEN}"

    # Check current webhook
    try:
        cur = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo", timeout=10).json()
        cur_url = cur.get("result", {}).get("url")
        if cur_url == webhook_url:
            print("Webhook already set; skipping set_webhook.")
            return
    except Exception:
        # proceed to set
        pass

    # Set webhook (with basic error handling, avoid repeated calls)
    try:
        bot.remove_webhook()
        bot.set_webhook(url=webhook_url)
        print("Webhook set to:", webhook_url)
    except Exception as e:
        print("Error setting webhook:", e)

# ----------------- Main -----------------
if __name__ == "__main__":
    # start worker in background
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()

    # ensure webhook
    ensure_webhook_set()

    print("BOT RUNNING ✅")
    # Run flask
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
