# app.py
# Telegram bot (webhook) + auto-check 30s optimized for 200+ UIDs
# - No Facebook Graph API token required
# - Check via avatar redirect + mbasic scraping (posts)
# - Save data to /tmp/uid_data.json
# - Notify admin when UID changes status (LIVE <-> DIE)
# Requirements: pyTelegramBotAPI Flask requests
# Deploy: set TELEGRAM_TOKEN, ADMIN_CHAT_ID, RENDER_SERVICE_NAME (or WEBHOOK_URL)

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
RENDER_SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "").strip()  # e.g. huy-telegram-bot
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # optional full url override
PORT = int(os.getenv("PORT", "10000"))
DATA_FILE = "/tmp/uid_data.json"  # persisted while instance alive
CHECK_INTERVAL = 30  # seconds per full loop (target)
MIN_DELAY_PER_UID = 0.10  # min delay between requests per uid to avoid bursts
MAX_DELAY_PER_UID = 0.5   # cap delay to be safe
REQUEST_TIMEOUT = 8

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required in env")

# -------------------- UTIL: persist --------------------
def load_data():
    try:
        if not os.path.exists(DATA_FILE):
            return {}
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(d):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# load to memory
UID_LIST = load_data()  # dict: uid -> {name,note,status,last_check,last_change}

# -------------------- TELEGRAM BOT + FLASK --------------------
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
app = Flask(__name__)

def main_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(
        KeyboardButton("/start"), KeyboardButton("/help"),
        KeyboardButton("/save"), KeyboardButton("/list"),
        KeyboardButton("/delete"), KeyboardButton("/check"),
        KeyboardButton("/checkdie"), KeyboardButton("/layanh"),
        KeyboardButton("/info")
    )
    return kb

# -------------------- FACEBOOK CHECK (no token) --------------------
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

def check_avatar_redirect(uid):
    """
    Request facebook profile picture URL (no redirect) by hitting the direct picture URL
    and inspecting redirect Location or content. Returns True if looks like a real avatar.
    """
    try:
        url = f"https://www.facebook.com/{uid}/picture?type=large"
        # do not follow redirects: we want to inspect the Location header
        r = session.get(url, allow_redirects=False, timeout=REQUEST_TIMEOUT)
        # If redirect (302) to scontent/... it's likely a real avatar
        if r.status_code in (301, 302):
            loc = r.headers.get("Location", "")
            if loc and ("scontent" in loc or "cdn" in loc):
                return True
            # sometimes redirect to safe_image or missing => treat as DIE
            return False
        # If status 200, check content for typical placeholders or login page
        if r.status_code == 200:
            txt = r.text[:500].lower()
            # if the content contains login or checkpoint, it's not a valid public avatar
            if "checkpoint" in txt or "facebook" in txt and ("login" in txt or "create a page" in txt):
                return False
            # content might be an image binary; treat as LIVE
            # try to detect if response is binary by headers
            ctype = r.headers.get("Content-Type", "")
            if "image" in ctype:
                return True
            # fallback: if length > small threshold
            if len(r.content) > 1000:
                return True
        return False
    except Exception:
        return False

def check_mbasic_posts(uid):
    """
    Scrape mbasic.facebook.com/{uid} and search for recent-post keywords.
    Returns True if there are recent posts or visible timeline.
    """
    try:
        url = f"https://mbasic.facebook.com/{uid}"
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return False
        txt = r.text
        # common indicators of activity
        keywords = ["đã đăng", "giờ trước", "phút trước", "vừa xong", "just now",
                    "hours ago", "minutes ago", "shared a post", "chia sẻ", "featuring"]
        lower = txt.lower()
        for k in keywords:
            if k in lower:
                return True
        # if page shows profile name and posts area, consider LIVE; check for "Contact" or "Info" patterns:
        if "profile.php" in url or "timeline" in lower or ("pagelet_timeline" in lower):
            # conservative: assume DIE unless we saw activity keywords
            return False
        return False
    except Exception:
        return False

def check_live_vps(uid):
    """
    Combined check: avatar redirect OR recent posts => LIVE, else DIE.
    Resilient: prefer avatar check first.
    """
    try:
        if check_avatar_redirect(uid):
            return "LIVE"
        if check_mbasic_posts(uid):
            return "LIVE"
        return "DIE"
    except Exception:
        return "DIE"

# -------------------- AUTO-CHECK WORKER --------------------
worker_lock = threading.Lock()
last_worker_error = 0

def send_status_change(admin_chat_id, uid, old, new, meta):
    try:
        t = time.strftime("%d/%m %H:%M", time.gmtime(time.time() + 7*3600))
        text = (f"🔔 <b>UID Thay Đổi Trạng Thái</b>\n"
                f"👤 UID: <code>{uid}</code>\n"
                f"👤 Tên: {meta.get('name','-')}\n"
                f"🔁 Trạng thái: <b>{old} → {new}</b>\n"
                f"🕰️ {t}")
        bot.send_message(int(admin_chat_id), text, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass

def auto_checker_loop():
    global UID_LIST, last_worker_error
    print("[WORKER] auto checker started. Target interval:", CHECK_INTERVAL, "s")
    while True:
        start_loop = time.time()
        try:
            # snapshot of UIDs to avoid mutation issues
            data = load_data()  # always read latest file
            if not isinstance(data, dict) or not data:
                # nothing to do
                time.sleep(5)
                continue

            uids = list(data.keys())
            num = max(1, len(uids))
            # compute per-uid delay to try finishing roughly in CHECK_INTERVAL
            per_uid_delay = max(MIN_DELAY_PER_UID, min(MAX_DELAY_PER_UID, CHECK_INTERVAL / num))
            # if per_uid_delay too small (< MIN), use MIN
            # iterate uid by uid
            for uid in uids:
                try:
                    meta = data.get(uid, {})
                    old_status = meta.get("status", "DIE")
                    # quick skip optimization: if last_check was very recent (< CHECK_INTERVAL/2), skip this uid
                    last_check = meta.get("last_check", 0)
                    if (time.time() - last_check) < (CHECK_INTERVAL * 0.5):
                        # we can skip some uids to spread load — but still do minimal pacing
                        time.sleep(min(per_uid_delay, 0.05))
                        continue

                    new_status = check_live_vps(uid)
                    # store update times
                    meta["last_check"] = int(time.time())
                    if new_status != old_status:
                        meta["status"] = new_status
                        meta["last_change"] = int(time.time())
                        data[uid] = meta
                        # persist immediately
                        save_data(data)
                        # inform admin
                        send_status_change(ADMIN_CHAT_ID, uid, old_status, new_status, meta)
                    else:
                        # update meta and persist occasionally
                        data[uid] = meta
                    # pacing
                    time.sleep(per_uid_delay)
                except Exception as e_uid:
                    # per-uid exception: just continue
                    # print("UID check error", uid, e_uid)
                    time.sleep(0.1)
            # after one pass, write the data
            save_data(data)
            # update in-memory
            UID_LIST = data
            # compute elapsed and sleep remaining to maintain CHECK_INTERVAL as approximate loop period
            elapsed = time.time() - start_loop
            if elapsed < CHECK_INTERVAL:
                time.sleep(max(1, CHECK_INTERVAL - elapsed))
            else:
                # if loop took longer than CHECK_INTERVAL, continue without additional sleep
                time.sleep(1)
        except Exception as e:
            last_worker_error = int(time.time())
            print("[WORKER] Exception:", e)
            # backoff a bit
            time.sleep(5)

# -------------------- COMMAND HANDLERS --------------------
user_flow = {}  # chat_id -> flow state

@bot.message_handler(commands=['start'])
def cmd_start(m):
    chat_id = m.chat.id
    if ADMIN_CHAT_ID and int(chat_id) != int(ADMIN_CHAT_ID):
        bot.send_message(chat_id, "❌ Bạn không có quyền sử dụng BOT này!\nLiên hệ admin để được duyệt.")
        return
    bot.send_message(chat_id, "✅ BOT đã sẵn sàng!", reply_markup=main_menu())

@bot.message_handler(commands=['help'])
def cmd_help(m):
    help_text = ("/save - Lưu UID\n/list - Xem UID\n/delete <uid> - Xóa UID\n/deleteall - Xóa tất cả\n"
                 "/check - Check tất cả UID ngay\n/checkdie <uid1 uid2 ...> - Check nhanh\n"
                 "/layanh <uid> - Lấy avatar + cover\n/info <uid> - Thông tin (scrape)")
    bot.send_message(m.chat.id, help_text)

@bot.message_handler(commands=['save'])
def cmd_save(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return bot.send_message(m.chat.id, "❌ Bạn không có quyền!")
    user_flow[m.chat.id] = {"step": 1}
    bot.send_message(m.chat.id, "🔵 Vui lòng gửi UID (số) hoặc link Facebook:")

@bot.message_handler(func=lambda m: m.chat.id in user_flow)
def handle_save_flow(m):
    chat = m.chat.id
    st = user_flow.get(chat, {})
    step = st.get("step", 1)
    txt = (m.text or "").strip()
    if step == 1:
        uid = None
        if txt.isdigit():
            uid = txt
        else:
            match = re.search(r"(?:profile\.php\?id=)?([0-9]{6,})", txt)
            if match:
                uid = match.group(1)
        if not uid:
            bot.send_message(chat, "❌ UID không hợp lệ. Gửi lại UID (số) hoặc link chứa id.")
            user_flow.pop(chat, None)
            return
        st["uid"] = uid
        st["step"] = 2
        bot.send_message(chat, "🔵 Nhập tên gợi nhớ cho UID:")
        return
    if step == 2:
        st["name"] = txt or "Không rõ"
        st["step"] = 3
        bot.send_message(chat, "🔵 Nhập ghi chú (note) cho UID:")
        return
    if step == 3:
        uid = st.get("uid")
        name = st.get("name", "Không rõ")
        note = txt or ""
        status = check_live_vps(uid)
        # update memory & persist
        UID_LIST[uid] = {"name": name, "note": note, "status": status, "last_check": int(time.time())}
        save_data(UID_LIST)
        user_flow.pop(chat, None)
        bot.send_message(chat, f"✅ Đã lưu UID <b>{uid}</b>\nTrạng thái: <b>{status}</b>")

@bot.message_handler(commands=['list'])
def cmd_list(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    data = load_data()
    if not data:
        return bot.send_message(m.chat.id, "⚠️ Danh sách rỗng.")
    parts = []
    for uid, meta in data.items():
        parts.append(f"• <b>{uid}</b> — {meta.get('name','-')} — {meta.get('status','-')}")
    bot.send_message(m.chat.id, "<b>📋 Danh sách UID:</b>\n" + "\n".join(parts), parse_mode="HTML")

@bot.message_handler(commands=['delete'])
def cmd_delete(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    sp = (m.text or "").split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /delete <uid>")
    uid = sp[1].strip()
    data = load_data()
    if uid in data:
        data.pop(uid, None)
        save_data(data)
        bot.send_message(m.chat.id, f"✅ Đã xóa UID {uid}")
    else:
        bot.send_message(m.chat.id, "❗ UID không tồn tại")

@bot.message_handler(commands=['deleteall'])
def cmd_deleteall(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    save_data({})
    bot.send_message(m.chat.id, "✅ Đã xóa toàn bộ UID")

@bot.message_handler(commands=['check'])
def cmd_check(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    data = load_data()
    if not data:
        return bot.send_message(m.chat.id, "⚠️ Danh sách rỗng.")
    out = []
    for uid in list(data.keys()):
        st = check_live_vps(uid)
        data[uid]["status"] = st
        data[uid]["last_check"] = int(time.time())
        out.append(f"{uid} → {st}")
        time.sleep(0.12)  # small breathing
    save_data(data)
    bot.send_message(m.chat.id, "<b>Kết quả:</b>\n" + "\n".join(out), parse_mode="HTML")

@bot.message_handler(commands=['checkdie'])
def cmd_checkdie(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    sp = (m.text or "").split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /checkdie <uid1> <uid2> ...")
    out = []
    for uid in sp[1:]:
        out.append(f"{uid} → {check_live_vps(uid)}")
        time.sleep(0.08)
    bot.send_message(m.chat.id, "\n".join(out))

@bot.message_handler(commands=['layanh'])
def cmd_layanh(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    sp = (m.text or "").split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /layanh <uid>")
    uid = sp[1].strip()
    # avatar: get redirect location
    try:
        r = session.get(f"https://www.facebook.com/{uid}/picture?type=large", allow_redirects=False, timeout=REQUEST_TIMEOUT)
        loc = r.headers.get("Location") or ""
        if loc:
            bot.send_message(m.chat.id, f"📷 Avatar:\n{loc}")
        else:
            bot.send_message(m.chat.id, "❌ Không lấy được avatar.")
    except Exception:
        bot.send_message(m.chat.id, "❌ Lỗi lấy avatar.")
    # cover: attempt via mbasic scraping
    try:
        r = session.get(f"https://mbasic.facebook.com/{uid}", timeout=REQUEST_TIMEOUT)
        text = r.text
        # try to find cover image url (search for 'src' with 'cover' nearby)
        m_cover = re.search(r'\"(https?:\/\/[^\"]*cover[^\"]*)\"', text)
        if m_cover:
            bot.send_message(m.chat.id, f"🖼 Cover:\n{m_cover.group(1)}")
        else:
            # find first large image as fallback
            m_img = re.search(r'src=\"(https?:\/\/[^\"]+\.jpg[^\"]*)\"', text)
            if m_img:
                bot.send_message(m.chat.id, f"🖼 Possible image:\n{m_img.group(1)}")
    except Exception:
        pass

@bot.message_handler(commands=['info'])
def cmd_info(m):
    if ADMIN_CHAT_ID and int(m.chat.id) != int(ADMIN_CHAT_ID):
        return
    sp = (m.text or "").split()
    if len(sp) < 2:
        return bot.send_message(m.chat.id, "❗ Dùng: /info <uid>")
    uid = sp[1].strip()
    try:
        r = session.get(f"https://mbasic.facebook.com/{uid}", timeout=REQUEST_TIMEOUT)
        txt = r.text
        # try to extract name
        m_name = re.search(r'<title>(.*?)</title>', txt, re.I|re.S)
        name = m_name.group(1).strip() if m_name else "-"
        # simple info extraction (may be limited)
        out = f"👤 Name: {name}\nURL: https://facebook.com/{uid}"
        bot.send_message(m.chat.id, out)
    except Exception:
        bot.send_message(m.chat.id, "❌ Lỗi lấy info.")

# auto-extract uid from posted link
@bot.message_handler(func=lambda m: m.text and ("facebook.com" in m.text or "fb.com" in m.text))
def msg_extract_uid(m):
    txt = m.text.strip()
    murl = re.search(r"https?://[^\s]+", txt)
    if not murl:
        return
    url = murl.group(0)
    m1 = re.search(r"(?:profile\.php\?id=)?([0-9]{6,})", url)
    if m1:
        bot.send_message(m.chat.id, f"✅ UID: <code>{m1.group(1)}</code>", parse_mode="HTML")
        return
    # fallback external service (best-effort)
    try:
        r = requests.post("https://id.traodoisub.com/api.php", data={"link": url}, timeout=6)
        j = r.json()
        if j.get("success") == 200 and j.get("id"):
            bot.send_message(m.chat.id, f"✅ UID: <code>{j['id']}</code>", parse_mode="HTML")
            return
    except Exception:
        pass
    bot.send_message(m.chat.id, "❌ Không thể lấy UID từ link này.")

# -------------------- WEBHOOK (Flask) --------------------
@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json(force=True)
    if update:
        try:
            bot.process_new_updates([types.Update.de_json(update)])
        except Exception as e:
            print("process update err:", e)
    return "OK", 200

def ensure_webhook_set():
    # determine service url
    if WEBHOOK_URL:
        service_url = WEBHOOK_URL.rstrip('/')
    elif RENDER_SERVICE_NAME:
        service_url = f"https://{RENDER_SERVICE_NAME}.onrender.com"
    else:
        service_url = os.getenv("RENDER_EXTERNAL_URL") or ""
        service_url = service_url.rstrip('/')
    if not service_url:
        print("[WEBHOOK] No service URL known - set webhook manually using Telegram API")
        return
    target = f"{service_url}/webhook/{TELEGRAM_TOKEN}"
    try:
        cur = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getWebhookInfo", timeout=6).json()
        cur_url = cur.get("result", {}).get("url", "")
        if cur_url != target:
            print("[WEBHOOK] Setting webhook to", target)
            requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={target}", timeout=10)
        else:
            print("[WEBHOOK] Webhook already set")
    except Exception as e:
        print("[WEBHOOK] error checking/setting webhook:", e)

# -------------------- START --------------------
if __name__ == "__main__":
    print("BOT starting...")
    # start worker thread
    t = threading.Thread(target=auto_checker_loop, daemon=True)
    t.start()
    # ensure webhook
    ensure_webhook_set()
    # run flask app
    app.run(host="0.0.0.0", port=PORT)
