# app.py
import os
import time
import json
import threading
import requests
from flask import Flask, request

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
FB_TOKEN = os.getenv("FB_TOKEN", "")
HOSTINGER_API_BASE = os.getenv("HOSTINGER_API_BASE", "").rstrip('/')
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
SECRET_KEY = os.getenv("SECRET_KEY", "")

from telebot import TeleBot, types
bot = TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")

app = Flask(__name__)

# --- Hostinger helpers ---
def _params(x):
    if SECRET_KEY: x["key"] = SECRET_KEY
    return x

def h_get(chat):
    r = requests.get(HOSTINGER_API_BASE, params=_params({"action":"get","chat_id":chat}), timeout=10)
    return r.json().get("data",{})

def h_save(chat,data):
    r = requests.post(HOSTINGER_API_BASE, params=_params({"action":"save","chat_id":chat}), json=data, timeout=10)
    return r.status_code==200

def h_update(chat,uid,payload):
    r = requests.post(HOSTINGER_API_BASE, params=_params({"action":"update_uid","chat_id":chat}), json={"uid":uid,"payload":payload}, timeout=10)
    return r.status_code==200

def h_delete(chat,uid):
    r = requests.post(HOSTINGER_API_BASE, params=_params({"action":"delete","chat_id":chat,"uid":uid}), timeout=10)
    return r.status_code==200

def h_ensure_chat(chat):
    # ensure chat listed in special _chat_list_
    cl = "_chat_list_"
    try:
        cur = h_get(cl)
        if not cur: cur=[]
        if chat not in cur:
            cur.append(chat)
            h_save(cl,cur)
    except: pass

# --- checkers ---
def chk_avatar(u):
    try:
        j=requests.get(f"https://graph.facebook.com/{u}/picture?type=large&redirect=0&access_token={FB_TOKEN}",timeout=5).json()
        url=j.get("data",{}).get("url","")
        if not url or "safe_image.php" in url or "scontent" not in url: return "DIE"
        return "LIVE"
    except: return "UNKNOWN"

def chk_cover(u):
    try:
        j=requests.get(f"https://graph.facebook.com/{u}?fields=cover&access_token={FB_TOKEN}",timeout=5).json()
        if j.get("cover"): return "LIVE"
        return "DIE"
    except: return "UNKNOWN"

def chk_post(u):
    try:
        r=requests.get(f"https://mbasic.facebook.com/{u}",headers={"User-Agent":"Mozilla"},timeout=5)
        if any(x in r.text for x in ["Đã đăng","Just now","hours ago","minutes ago","giờ trước","phút trước"]):
            return "LIVE"
        return "DIE"
    except: return "UNKNOWN"

def check_uid(u):
    s=[]
    a=chk_avatar(u); 
    if a=="LIVE": s.append(1)
    elif a=="UNKNOWN": return "UNKNOWN"
    c=chk_cover(u); 
    if c=="LIVE": s.append(1)
    p=chk_post(u); 
    if p=="LIVE": s.append(1)
    return "LIVE" if s else "DIE"

def send_status(chat,uid,m,pre,new):
    try:
        t=time.strftime("%d/%m %H:%M",time.gmtime(time.time()+7*3600))
        bot.send_message(chat,f"{uid} đổi {pre}➜{new} lúc {t}",disable_web_page_preview=True)
    except:pass

def worker():
    while 1:
        try:
            cl=h_get("_chat_list_") or [str(ADMIN_CHAT_ID)]
            for c in cl:
                us=h_get(c) or {}
                for uid,v in us.items():
                    n=check_uid(uid)
                    if n!=v.get("status"):
                        v["status"]=n; v["last_notified"]=int(time.time())
                        h_update(c,uid,v)
                        send_status(c,uid,v,v.get("status"),n)
                    time.sleep(0.7)
        except:pass
        time.sleep(30)

@app.route('/webhook/'+TELEGRAM_TOKEN,methods=['POST'])
def wh():
    bot.process_new_updates([types.Update.de_json(request.json)])
    return "",200

@app.route('/')
def home(): return "RUNNING"

# --- bot commands ---
flow={}
@bot.message_handler(commands=['start'])
def st(m): h_ensure_chat(str(m.chat.id)); bot.reply_to(m,"OK")

@bot.message_handler(commands=['save'])
def sv(m):
    flow[m.chat.id]={"s":1}; bot.send_message(m.chat.id,"Gửi UID")

@bot.message_handler(func=lambda m:m.chat.id in flow)
def fl(m):
    c=flow[m.chat.id]
    if c["s"]==1:
        uid=m.text.strip()
        if not uid.isdigit(): return bot.send_message(m.chat.id,"Sai UID")
        c["uid"]=uid; c["s"]=2; bot.send_message(m.chat.id,"Tên?")
    elif c["s"]==2:
        c["name"]=m.text; c["s"]=3; bot.send_message(m.chat.id,"Note?")
    else:
        uid,name,nt=c["uid"],c["name"],m.text
        stt=check_uid(uid)
        us=h_get(str(m.chat.id)) or {}
        us[uid]={"name":name,"note":nt,"status":stt,"chat_id":str(m.chat.id)}
        h_save(str(m.chat.id),us); h_ensure_chat(str(m.chat.id))
        bot.send_message(m.chat.id,f"Đã lưu {uid} - {stt}"); flow.pop(m.chat.id)

if __name__=='__main__':
    threading.Thread(target=worker,daemon=True).start()
    whurl=(os.getenv("RENDER_EXTERNAL_URL") or "")+f"/webhook/{TELEGRAM_TOKEN}"
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",params={"url":whurl})
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",10000)))
