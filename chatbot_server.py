"""
Dog and Cat Lovely — Chatbot Server
=====================================
Webhook server สำหรับ LINE OA และ Facebook Messenger
  • Auto-reply ตามคำถามที่พบบ่อย
  • ดึงข้อมูลจาก drx_data.json (real-time จาก DRX)
  • Flex Message สวยงามสำหรับ LINE OA

ติดตั้ง:
  pip install flask requests

รัน:
  python chatbot_server.py

Expose ด้วย ngrok (ทดสอบ):
  ngrok http 5000
  → ได้ URL เช่น https://xxxx.ngrok.io
  → ตั้งเป็น Webhook URL ใน LINE / FB

Production:
  ใช้ Gunicorn + Nginx หรือ deploy บน Railway / Render / Azure

============================================================
LINE Webhook Setup:
  1. https://developers.line.biz → Channel settings
  2. Messaging API → Webhook URL: https://yourdomain/webhook/line
  3. Enable "Use webhook"
  4. Disable "Auto-reply messages" (ให้ bot ตอบเอง)

Facebook Webhook Setup:
  1. developers.facebook.com → App → Messenger → Settings
  2. Webhooks → Add callback URL: https://yourdomain/webhook/fb
  3. Verify token: ใส่ค่า FB_VERIFY_TOKEN ด้านล่าง
  4. Subscribe: messages, messaging_postbacks
============================================================
"""

import json
import re
import os
import hashlib
import hmac
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, abort
import requests as req

# โหลด .env ถ้ามี (สำหรับ local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv ไม่ได้ติดตั้ง ใช้ env vars จาก OS ได้เลย

# ──────────────────────────────────────────────
#  CONFIG  (อ่านจาก environment variables)
# ──────────────────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_TOKEN", "")
LINE_CHANNEL_SECRET       = os.environ.get("LINE_SECRET", "")
FB_PAGE_ACCESS_TOKEN      = os.environ.get("FB_TOKEN",  "")
FB_VERIFY_TOKEN           = os.environ.get("FB_VERIFY", "dogcatlovely_verify_2026")
CLINIC_PHONE              = "02-XXX-XXXX"   # ← ใส่เบอร์คลินิก
CLINIC_LINE_OA            = "@dogcatlovely" # ← LINE OA ID
DATA_FILE                 = Path(__file__).parent / "drx_data.json"

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  DATA LOADER
# ──────────────────────────────────────────────
_data_cache: dict = {}
_cache_time: datetime | None = None
CACHE_TTL_SEC = 300  # refresh ทุก 5 นาที


def get_data() -> dict:
    global _data_cache, _cache_time
    now = datetime.now()
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_TTL_SEC:
        return _data_cache
    if DATA_FILE.exists():
        try:
            _data_cache = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            _cache_time = now
            log.info("Data reloaded from drx_data.json")
        except Exception as e:
            log.error(f"Failed to reload data: {e}")
    return _data_cache


# ──────────────────────────────────────────────
#  INTENT DETECTION
# ──────────────────────────────────────────────
INTENTS = {
    "greeting": [
        r"สวัสดี", r"หวัดดี", r"ดีครับ", r"ดีค่ะ", r"hello", r"hi",
        r"ว่าไง", r"มีใครอยู่ไหม",
    ],
    "hours": [
        r"เวลาเปิด", r"เวลาทำการ", r"กี่โมง", r"เปิดกี่โมง", r"ปิดกี่โมง",
        r"วันไหน", r"วันเสาร์", r"วันอาทิตย์", r"วันหยุด", r"วันจันทร์",
        r"เปิดไหม", r"ยังเปิดอยู่",
    ],
    "price": [
        r"ราคา", r"ค่าใช้จ่าย", r"ค่าตรวจ", r"ค่า", r"เท่าไร", r"เท่าไหร่",
        r"กี่บาท", r"แพงไหม",
    ],
    "appointment": [
        r"นัด", r"จอง", r"ลงทะเบียน", r"ต้องการนัด", r"อยากนัด",
        r"ขอนัด", r"นัดหมาย", r"appointment",
    ],
    "check_appointment": [
        r"ตรวจสอบนัด", r"เช็คนัด", r"นัดวันไหน", r"นัดกี่โมง",
        r"ผมนัด", r"หนูนัด", r"ดูนัด",
    ],
    "pet_status": [
        r"สถานะ", r"ยังอยู่ไหม", r"เป็นยังไง", r"อาการ", r"ดีขึ้นไหม",
        r"รักษาอยู่", r"admit", r"แอดมิท", r"นอน", r"ค้างคืน",
    ],
    "vaccine": [
        r"วัคซีน", r"ฉีดยา", r"rabies", r"พิษสุนัขบ้า", r"7 โรค", r"9 โรค",
    ],
    "emergency": [
        r"ฉุกเฉิน", r"ด่วน", r"urgent", r"emergency", r"หายใจไม่ออก",
        r"ชัก", r"หมดสติ", r"เลือดออก", r"กินยา", r"วิกฤต",
    ],
    "location": [
        r"ที่ตั้ง", r"อยู่ที่ไหน", r"แผนที่", r"เส้นทาง", r"location",
        r"address", r"ที่อยู่",
    ],
    "services": [
        r"บริการ", r"ทำอะไรบ้าง", r"มีอะไรบ้าง", r"service",
        r"ตรวจ", r"รักษา", r"ผ่าตัด", r"เอ็กซเรย์", r"lab",
    ],
    "staff": [
        r"สัตวแพทย์", r"หมอ", r"ทีม", r"หมอชื่อ", r"vet",
    ],
    "farewell": [
        r"ขอบคุณ", r"ขอบใจ", r"thanks", r"thank", r"bye", r"ลาก่อน",
    ],
}


def detect_intent(text: str) -> str:
    text_lo = text.lower()
    for intent, patterns in INTENTS.items():
        for p in patterns:
            if re.search(p, text_lo):
                return intent
    return "unknown"


# ──────────────────────────────────────────────
#  RESPONSE GENERATOR
# ──────────────────────────────────────────────
THAI_MONTHS = ["","ม.ค.","ก.พ.","มี.ค.","เม.ย.","พ.ค.","มิ.ย.",
               "ก.ค.","ส.ค.","ก.ย.","ต.ค.","พ.ย.","ธ.ค."]

def thai_date_short() -> str:
    n = datetime.now()
    return f"{n.day} {THAI_MONTHS[n.month]} {n.year+543}"


def get_today_appointments() -> list:
    data = get_data()
    appts = data.get("appointments", [])
    today = datetime.now().strftime("%Y-%m-%d")
    return [a for a in appts if a.get("date", "") == today]


def get_vets() -> list:
    data = get_data()
    return data.get("vets", [])


def build_response(intent: str, user_msg: str = "") -> str:
    """สร้างข้อความตอบกลับตาม intent"""

    if intent == "greeting":
        now_h = datetime.now().hour
        greeting = "สวัสดีตอนเช้า" if now_h < 12 else ("สวัสดีตอนบ่าย" if now_h < 17 else "สวัสดีตอนเย็น")
        return (
            f"🐾 {greeting}ครับ!\n"
            "ยินดีต้อนรับสู่ Dog and Cat Lovely Pet Hospital\n\n"
            "เราช่วยอะไรได้บ้างครับ? พิมพ์คำถามได้เลย เช่น:\n"
            "• เวลาเปิด-ปิด\n"
            "• ราคาค่าตรวจ\n"
            "• ขอนัดหมาย\n"
            "• สถานะสัตว์ที่รักษา\n"
            "• ตำแหน่งที่ตั้ง\n\n"
            f"📞 โทร {CLINIC_PHONE} (มีเจ้าหน้าที่รับตลอดเวลาทำการ)"
        )

    elif intent == "hours":
        return (
            "🕐 เวลาทำการ Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📅 จันทร์ – ศุกร์\n"
            "   🌅 08:00 – 12:00 น.\n"
            "   🌞 13:00 – 20:00 น.\n\n"
            "📅 เสาร์ – อาทิตย์\n"
            "   🌅 08:00 – 20:00 น. (ไม่หยุดพัก)\n\n"
            "🚨 กรณีฉุกเฉินนอกเวลา\n"
            f"   📞 โทร {CLINIC_PHONE}\n\n"
            "ℹ️ วันหยุดนักขัตฤกษ์อาจมีการปรับเวลา\n"
            "   โปรดโทรสอบถามล่วงหน้าครับ"
        )

    elif intent == "price":
        return (
            "💰 ราคาบริการ (โดยประมาณ)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🩺 ค่าตรวจทั่วไป      ฿150 – 300\n"
            "💉 ฉีดวัคซีน         ฿300 – 600\n"
            "🧪 เจาะเลือด (CBC)    ฿400 – 800\n"
            "🔬 เพาะเชื้อ / Lab    ฿300 – 1,500\n"
            "📷 เอ็กซเรย์          ฿500 – 1,500\n"
            "🛁 อาบน้ำ-ตัดขน       ฿300 – 800\n"
            "🏥 ค่า Admit (ต่อวัน) ฿500 – 2,000\n\n"
            "ℹ️ ราคาอาจแตกต่างตามน้ำหนักและความซับซ้อน\n"
            f"📞 โทรสอบถามเพิ่มเติม: {CLINIC_PHONE}"
        )

    elif intent == "appointment":
        today_appts = get_today_appointments()
        appt_info = f"วันนี้มีนัดแล้ว {len(today_appts)} ราย" if today_appts else "วันนี้ยังมีเวลาว่าง"
        return (
            "📅 นัดหมายกับ Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 สถานะวันนี้ ({thai_date_short()}): {appt_info}\n\n"
            "วิธีนัดหมาย:\n"
            f"📞 โทร {CLINIC_PHONE}\n"
            f"💬 LINE OA: {CLINIC_LINE_OA}\n"
            "🕐 รับนัดล่วงหน้าตั้งแต่ 08:00 น.\n\n"
            "กรุณาแจ้ง:\n"
            "• ชื่อเจ้าของและชื่อสัตว์เลี้ยง\n"
            "• ประเภทสัตว์ (สุนัข/แมว/อื่นๆ)\n"
            "• อาการเบื้องต้น\n"
            "• วันและเวลาที่ต้องการ"
        )

    elif intent == "check_appointment":
        return (
            "🔍 ตรวจสอบนัดหมาย\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "กรุณาแจ้งข้อมูลใดข้อหนึ่ง:\n"
            "• HN (หมายเลขผู้ป่วย) เช่น HN-690001-1\n"
            "• ชื่อสัตว์เลี้ยง + ชื่อเจ้าของ\n\n"
            "หรือโทรตรวจสอบได้ที่:\n"
            f"📞 {CLINIC_PHONE}"
        )

    elif intent == "pet_status":
        return (
            "🏥 ตรวจสอบสถานะสัตว์ที่รักษา\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "กรุณาแจ้งข้อมูล:\n"
            "• HN (หมายเลขผู้ป่วย)\n"
            "• ชื่อสัตว์\n\n"
            "🔒 ข้อมูลสุขภาพเป็นความลับ\n"
            "เราจะยืนยันตัวตนก่อนให้ข้อมูลนะครับ\n\n"
            f"📞 โทรสอบถามโดยตรง: {CLINIC_PHONE}\n"
            "⏰ (ตลอดเวลาทำการ)"
        )

    elif intent == "vaccine":
        return (
            "💉 บริการฉีดวัคซีน\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🐶 สุนัข:\n"
            "  • วัคซีนรวม 7 / 9 โรค\n"
            "  • พิษสุนัขบ้า\n"
            "  • หัดสุนัข, ไข้หัด, ลำไส้อักเสบ\n\n"
            "🐱 แมว:\n"
            "  • วัคซีนรวม 3 / 4 โรค\n"
            "  • พิษสุนัขบ้า\n"
            "  • ไข้หัดแมว\n\n"
            "📋 กำหนดการฉีด:\n"
            "  • ลูกสุนัข/แมว: เริ่มที่ 6-8 สัปดาห์\n"
            "  • กระตุ้นทุกปี\n\n"
            f"📞 นัดฉีดวัคซีน: {CLINIC_PHONE}"
        )

    elif intent == "emergency":
        return (
            "🚨 กรณีฉุกเฉิน\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📞 โทรทันที: {CLINIC_PHONE}\n\n"
            "⚡ สัญญาณฉุกเฉินที่ต้องมาด่วน:\n"
            "• หายใจลำบาก / หอบ\n"
            "• ชัก / หมดสติ\n"
            "• เลือดออกมาก\n"
            "• กินสารพิษ\n"
            "• ปัสสาวะไม่ออกนานกว่า 12 ชม.\n"
            "• อาเจียน/ท้องเสียรุนแรง\n\n"
            "❗ อย่ารอ — มาโรงพยาบาลได้เลยครับ\n"
            "🕐 รับฉุกเฉินตลอดเวลาทำการ"
        )

    elif intent == "location":
        return (
            "📍 ที่ตั้ง Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🏥 โรงพยาบาลสัตว์หมาแมวเลิฟลี่\n"
            "📌 ที่อยู่: [ใส่ที่อยู่จริงที่นี่]\n"
            "🗺️ Google Maps: [ใส่ลิงค์ Maps]\n\n"
            "🚗 การเดินทาง:\n"
            "  • [ใส่เส้นทางจาก BTS/MRT]\n"
            "  • [ที่จอดรถ]\n\n"
            f"📞 โทรสอบถามเส้นทาง: {CLINIC_PHONE}"
        )

    elif intent == "services":
        return (
            "🏥 บริการของเรา\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🩺 ตรวจรักษาทั่วไป\n"
            "💉 ฉีดวัคซีน\n"
            "🔬 ตรวจ Lab / เพาะเชื้อ\n"
            "📷 เอ็กซเรย์ / อัลตราซาวด์\n"
            "🏥 Admit / ค้างคืน\n"
            "✂️ ผ่าตัด\n"
            "🛁 อาบน้ำ-ตัดขน\n"
            "💊 ขายยา / Pet Shop\n"
            "🦷 ทำฟัน / Dental\n"
            "📋 ตรวจสุขภาพประจำปี\n\n"
            f"📞 สอบถามเพิ่มเติม: {CLINIC_PHONE}"
        )

    elif intent == "staff":
        vets = get_vets()
        if vets:
            vet_list = "\n".join(f"  • {v.get('name', v)}" for v in vets[:5])
            return (
                "👩‍⚕️ ทีมสัตวแพทย์ของเรา\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"{vet_list}\n\n"
                f"📞 นัดพบสัตวแพทย์: {CLINIC_PHONE}"
            )
        return (
            "👩‍⚕️ ทีมสัตวแพทย์ผู้เชี่ยวชาญพร้อมดูแลสัตว์เลี้ยงของคุณ\n"
            f"📞 สอบถามและนัดหมาย: {CLINIC_PHONE}"
        )

    elif intent == "farewell":
        return (
            "😊 ขอบคุณที่ติดต่อมาครับ!\n"
            "🐾 ดูแลน้องให้ดีนะครับ\n"
            f"📞 มีอะไรสงสัยโทรหาได้เลย: {CLINIC_PHONE}\n"
            "Dog and Cat Lovely 🏥"
        )

    else:  # unknown
        return (
            "🤔 ขออภัยครับ ไม่แน่ใจว่าต้องการสอบถามเรื่องอะไร\n\n"
            "ลองพิมพ์คำเหล่านี้ได้ครับ:\n"
            "• \"เวลาเปิด\"\n"
            "• \"ราคาค่าตรวจ\"\n"
            "• \"ขอนัดหมาย\"\n"
            "• \"บริการมีอะไรบ้าง\"\n"
            "• \"ฉุกเฉิน\"\n\n"
            f"หรือโทรคุยกับเจ้าหน้าที่: {CLINIC_PHONE}\n"
            "⏰ (ตามเวลาทำการ)"
        )


# ──────────────────────────────────────────────
#  LINE FLEX MESSAGE (สำหรับตอบกลับสวยงาม)
# ──────────────────────────────────────────────
def build_flex_greeting() -> dict:
    """Flex Message หน้าแรก — แสดงเมนูหลัก"""
    return {
        "type": "flex",
        "altText": "🐾 ยินดีต้อนรับสู่ Dog and Cat Lovely",
        "contents": {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#FF8C00",
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "🐾 Dog and Cat Lovely",
                     "color": "#FFFFFF", "size": "xl", "weight": "bold"},
                    {"type": "text", "text": "Pet Hospital — ยินดีต้อนรับครับ",
                     "color": "#FFE0B2", "size": "sm"},
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "เราช่วยอะไรได้บ้างครับ?",
                     "weight": "bold", "size": "md", "margin": "md"},
                    {"type": "separator", "margin": "md"},
                    _menu_row("🕐", "เวลาทำการ",     "เวลาเปิด-ปิด วันหยุด"),
                    _menu_row("💰", "ราคาค่าบริการ", "ค่าตรวจ ค่าวัคซีน"),
                    _menu_row("📅", "นัดหมาย",       "จองคิว ตรวจสอบนัด"),
                    _menu_row("🏥", "บริการ",        "ตรวจ รักษา ผ่าตัด"),
                    _menu_row("📍", "ที่ตั้ง",        "แผนที่ การเดินทาง"),
                    _menu_row("🚨", "ฉุกเฉิน",       "โทรด่วนทันที"),
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "action": {"type": "uri", "label": f"📞 โทร {CLINIC_PHONE}",
                                   "uri": f"tel:{CLINIC_PHONE.replace('-','')}"},
                        "color": "#FF8C00",
                        "style": "primary",
                    }
                ]
            }
        }
    }


def _menu_row(icon: str, title: str, subtitle: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "margin": "sm",
        "contents": [
            {"type": "text", "text": icon, "flex": 1, "gravity": "center"},
            {"type": "box", "layout": "vertical", "flex": 8, "contents": [
                {"type": "text", "text": title, "weight": "bold", "size": "sm"},
                {"type": "text", "text": subtitle, "color": "#888888", "size": "xs"},
            ]},
        ]
    }


# ──────────────────────────────────────────────
#  LINE API HELPERS
# ──────────────────────────────────────────────
LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"
LINE_PUSH_URL  = "https://api.line.me/v2/bot/message/push"


def line_reply(reply_token: str, messages: list):
    """ส่ง reply กลับไปยัง LINE"""
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"replyToken": reply_token, "messages": messages}
    r = req.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=10)
    if r.status_code != 200:
        log.warning(f"LINE reply failed: {r.status_code} {r.text[:200]}")


def line_reply_text(reply_token: str, text: str):
    line_reply(reply_token, [{"type": "text", "text": text}])


def line_reply_flex(reply_token: str, flex: dict):
    line_reply(reply_token, [flex])


def verify_line_signature(body: bytes, signature: str) -> bool:
    """ยืนยัน LINE webhook signature"""
    if not LINE_CHANNEL_SECRET or LINE_CHANNEL_SECRET == "YOUR_LINE_CHANNEL_SECRET":
        return True  # dev mode: skip verification
    expected = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body, hashlib.sha256
    ).digest()
    import base64
    return hmac.compare_digest(
        base64.b64encode(expected).decode(),
        signature
    )


# ──────────────────────────────────────────────
#  FB MESSENGER API HELPERS
# ──────────────────────────────────────────────
FB_SEND_URL = "https://graph.facebook.com/v18.0/me/messages"


def fb_send_message(recipient_id: str, text: str):
    """ส่งข้อความกลับไปยัง FB Messenger"""
    params  = {"access_token": FB_PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": recipient_id},
        "message":   {"text": text},
    }
    r = req.post(FB_SEND_URL, params=params, json=payload, timeout=10)
    if r.status_code != 200:
        log.warning(f"FB reply failed: {r.status_code} {r.text[:200]}")


# ──────────────────────────────────────────────
#  QUICK REPLIES (LINE)
# ──────────────────────────────────────────────
def quick_replies() -> list:
    """Quick reply buttons สำหรับ LINE"""
    items = [
        ("🕐 เวลาเปิด",    "เวลาเปิด"),
        ("💰 ราคา",        "ราคาค่าตรวจ"),
        ("📅 นัดหมาย",     "ขอนัดหมาย"),
        ("📍 ที่ตั้ง",      "ที่ตั้ง"),
        ("🚨 ฉุกเฉิน",     "ฉุกเฉิน"),
    ]
    return [
        {"type": "action", "action": {"type": "message", "label": label, "text": text}}
        for label, text in items
    ]


# ──────────────────────────────────────────────
#  ROUTES — LINE WEBHOOK
# ──────────────────────────────────────────────
@app.route("/webhook/line", methods=["POST"])
def line_webhook():
    body      = request.get_data()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_line_signature(body, signature):
        log.warning("Invalid LINE signature")
        abort(403)

    events = request.json.get("events", [])
    for event in events:
        handle_line_event(event)

    return jsonify({"status": "ok"})


def handle_line_event(event: dict):
    event_type = event.get("type")
    reply_token = event.get("replyToken")

    if event_type == "message":
        msg_type = event.get("message", {}).get("type")
        if msg_type == "text":
            user_text = event.get("message", {}).get("text", "").strip()
            log.info(f"LINE msg: {user_text!r}")

            intent = detect_intent(user_text)
            log.info(f"Intent: {intent}")

            if intent == "greeting":
                # ส่ง Flex Message สำหรับทักทาย
                flex = build_flex_greeting()
                line_reply_flex(reply_token, flex)
            else:
                response = build_response(intent, user_text)
                # ส่งพร้อม quick replies
                msgs = [{
                    "type": "text",
                    "text": response,
                    "quickReply": {"items": quick_replies()},
                }]
                line_reply(reply_token, msgs)

    elif event_type == "follow":
        # ผู้ใช้ติดตาม LINE OA ใหม่
        welcome = build_response("greeting")
        flex = build_flex_greeting()
        line_reply_flex(reply_token, flex)

    elif event_type == "postback":
        data = event.get("postback", {}).get("data", "")
        log.info(f"Postback: {data}")


# ──────────────────────────────────────────────
#  ROUTES — FACEBOOK MESSENGER WEBHOOK
# ──────────────────────────────────────────────
@app.route("/webhook/fb", methods=["GET"])
def fb_verify():
    """Facebook webhook verification"""
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == FB_VERIFY_TOKEN:
        log.info("Facebook webhook verified!")
        return challenge, 200
    else:
        log.warning(f"FB verification failed: mode={mode} token={token}")
        abort(403)


@app.route("/webhook/fb", methods=["POST"])
def fb_webhook():
    data = request.json
    if data.get("object") != "page":
        return "ok", 200

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            handle_fb_event(event)

    return "ok", 200


def handle_fb_event(event: dict):
    sender_id = event.get("sender", {}).get("id")
    if not sender_id:
        return

    if "message" in event:
        msg = event["message"]
        if msg.get("is_echo"):
            return  # skip our own messages
        user_text = msg.get("text", "").strip()
        log.info(f"FB msg from {sender_id}: {user_text!r}")

        intent = detect_intent(user_text)
        response = build_response(intent, user_text)
        fb_send_message(sender_id, response)

    elif "postback" in event:
        payload = event["postback"].get("payload", "")
        log.info(f"FB postback: {payload}")
        fb_send_message(sender_id, build_response("greeting"))


# ──────────────────────────────────────────────
#  ROUTES — HEALTH / STATUS
# ──────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    data = get_data()
    meta = data.get("_meta", {})
    return jsonify({
        "status":    "running",
        "app":       "Dog and Cat Lovely Chatbot",
        "version":   "2.0",
        "endpoints": {
            "LINE webhook": "/webhook/line",
            "FB webhook":   "/webhook/fb",
        },
        "drx_data": {
            "fetched_at": meta.get("fetched_at", "N/A"),
            "cases":      len(data.get("cases", [])),
            "appts":      len(data.get("appointments", [])),
        }
    })


@app.route("/test/<intent>", methods=["GET"])
def test_response(intent: str):
    """ทดสอบ response โดยไม่ต้องส่ง LINE/FB"""
    return jsonify({
        "intent":   intent,
        "response": build_response(intent),
    })


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print("=" * 50)
    print("  🐾 Dog and Cat Lovely Chatbot Server")
    print("=" * 50)
    print(f"  LINE Token : {'✅ ตั้งค่าแล้ว' if LINE_CHANNEL_ACCESS_TOKEN != 'YOUR_LINE_CHANNEL_ACCESS_TOKEN' else '⚠️  ยังไม่ตั้งค่า'}")
    print(f"  FB Token   : {'✅ ตั้งค่าแล้ว' if FB_PAGE_ACCESS_TOKEN != 'YOUR_FB_PAGE_ACCESS_TOKEN' else '⚠️  ยังไม่ตั้งค่า'}")
    print()
    print("  Endpoints:")
    print("    GET  /                  → status")
    print("    POST /webhook/line      → LINE webhook")
    print("    GET  /webhook/fb        → FB verification")
    print("    POST /webhook/fb        → FB messages")
    print("    GET  /test/<intent>     → test response")
    print()
    print("  Test intents: greeting, hours, price, appointment,")
    print("                vaccine, emergency, location, services")
    print()
    print(f"  🌐 http://0.0.0.0:{port}")
    print("=" * 50)

    app.run(host="0.0.0.0", port=port, debug=debug_mode)
