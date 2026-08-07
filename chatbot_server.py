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
from flask import Flask, request, jsonify, abort, send_from_directory
import requests as req

# ── Bangkok timezone helper — Railway รัน UTC แต่ user อยู่ไทย (UTC+7) ──
try:
    from zoneinfo import ZoneInfo
    _BKK_TZ = ZoneInfo("Asia/Bangkok")
except ImportError:
    try:
        import pytz
        _BKK_TZ = pytz.timezone("Asia/Bangkok")
    except ImportError:
        _BKK_TZ = None

def bkk_now() -> datetime:
    """คืน datetime ใน Asia/Bangkok เสมอ (UTC+7) — ใช้แทน datetime.now() ทุกที่ที่ส่งให้ user"""
    if _BKK_TZ is not None:
        return datetime.now(_BKK_TZ).replace(tzinfo=None)
    return datetime.now()

# โหลด .env ถ้ามี (สำหรับ local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass  # dotenv ไม่ได้ติดตั้ง ใช้ env vars จาก OS ได้เลย

# โหลด real replies จาก Facebook history
from bot_replies import get_reply as _get_real_reply, REAL_REPLIES, FOOTER

# โหลด Q&A database จาก Excel
import qa_database as qa

# โหลด appointment DB (Excel-backed)
try:
    import appointment_db as adb
    APPOINTMENT_DB_READY = True
except Exception as _e:
    APPOINTMENT_DB_READY = False
    adb = None
    print(f"[WARN] appointment_db not loaded: {_e}", file=sys.stderr)

# ──────────────────────────────────────────────
#  CONFIG  (อ่านจาก environment variables)
# ──────────────────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_TOKEN", "")
LINE_CHANNEL_SECRET       = os.environ.get("LINE_SECRET", "")
FB_PAGE_ACCESS_TOKEN      = os.environ.get("FB_TOKEN",  "")
FB_VERIFY_TOKEN           = os.environ.get("FB_VERIFY", "dogcatlovely_verify_2026")  # set FB_VERIFY env var in production
# Lovely Bot → แจ้งเตือน admin เมื่อบอทตอบไม่ได้
LOVELY_BOT_TOKEN          = os.environ.get("LOVELY_BOT_TOKEN", "")
ADMIN_LINE_ID             = os.environ.get("LINE_TARGET_ID", "Ude09abe7b1f73ee901c047ccfe693dd8").strip()
CLINIC_PHONE              = "080-4288181"    # สาขาราชวิถี (หลัก)

# ── ชั่วคราว: log ผู้ส่งล่าสุด ใช้ debug หา userId จริงตอน channel เปลี่ยน ──
_recent_senders: list = []
CLINIC_PHONE2             = "090-1556446"   # สาขาหลังม.ศิลปากร
CLINIC_LINE_OA            = "@dogcatlovely" # LINE OA
CLINIC_LINE_URL           = "https://lin.ee/7bV54ar"
CLINIC_MAP1               = "https://maps.app.goo.gl/HcZtm5Hq2EDbX2YNA"  # ราชวิถี
CLINIC_MAP2               = "https://maps.app.goo.gl/LhX7jBXETBDcw3jC9"  # หลังม.ศิลปากร

# URL ภายนอกสำหรับ serve รูป (ngrok / production)
NGROK_DOMAIN              = os.environ.get("NGROK_DOMAIN", "stream-cannon-monogamy.ngrok-free.dev").strip()
PUBLIC_BASE_URL           = os.environ.get("PUBLIC_BASE_URL", f"https://{NGROK_DOMAIN}").strip().rstrip("/")
DATA_FILE                 = Path(__file__).parent / "drx_data.json"

app = Flask(__name__)
app.json.ensure_ascii = False  # ให้ภาษาไทยแสดงตรงๆ ไม่ escape เป็น \uXXXX
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


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
# NOTE: ลำดับสำคัญ — intent เฉพาะเจาะจง (cat/dog) ต้องอยู่ก่อน generic
INTENTS = {
    "greeting": [
        r"สวัสดี", r"หวัดดี", r"ดีครับ", r"ดีค่ะ", r"hello", r"hi",
        r"ว่าไง", r"มีใครอยู่ไหม",
    ],
    "hours": [
        r"เวลาเปิด", r"เวลาทำการ", r"กี่โมง", r"เปิดกี่โมง", r"ปิดกี่โมง",
        r"เปิดไหม", r"ยังเปิดอยู่", r"เปิดทุกวัน",
    ],
    # ── เฉพาะ cat/dog ก่อน ──
    "neuter_cat": [
        r"ทำหมัน.*แมว", r"แมว.*ทำหมัน", r"หมันแมว", r"ผ่าหมัน.*แมว",
    ],
    "neuter_dog": [
        r"ทำหมัน.*(หมา|สุนัข|น้องหมา)", r"(หมา|สุนัข).*ทำหมัน",
        r"หมันหมา", r"หมันสุนัข",
    ],
    "csection": [
        r"ผ่าคลอด", r"คลอด", r"ช่วยคลอด", r"ทำคลอด", r"caesarean", r"c-section", r"csection",
    ],
    "neuter": [
        r"ทำหมัน", r"ผ่าหมัน", r"spay", r"neuter", r"ราคาทำหมัน",
    ],
    "vaccine_cat": [
        r"วัคซีน.*แมว", r"แมว.*วัคซีน", r"ฉีด.*แมว",
        r"ไข้หัดแมว", r"ลิวคีเมีย",
    ],
    "vaccine_dog": [
        r"วัคซีน.*(หมา|สุนัข|น้องหมา)", r"(หมา|สุนัข).*วัคซีน",
        r"ฉีด.*(หมา|สุนัข)", r"หัดสุนัข",
    ],
    "vaccine": [
        r"วัคซีน", r"ฉีดวัคซีน", r"rabies", r"พิษสุนัขบ้า",
        r"ราคาวัคซีน",
    ],
    "flea_cat": [
        r"(เห็บ|หมัด).*แมว", r"แมว.*(เห็บ|หมัด)",
        r"advocate", r"broadline",
    ],
    "flea_dog": [
        r"(เห็บ|หมัด).*(หมา|สุนัข)", r"(หมา|สุนัข).*(เห็บ|หมัด)",
        r"nexgard",
    ],
    "flea": [
        r"เห็บ", r"หมัด", r"bravecto",
        r"ป้องกันเห็บ", r"ยาเห็บ", r"ยาหมัด", r"กำจัดเห็บ",
    ],
    "boarding": [
        r"ฝากเลี้ยง", r"ฝากไว้", r"ฝากแมว", r"ฝากหมา",
        r"ราคาฝาก", r"ค่าฝาก", r"boarding", r"hotel",
    ],
    "bath": [
        r"อาบน้ำ", r"ตัดขน", r"grooming", r"แต่งขน",
    ],
    "appointment": [
        r"นัด", r"จอง", r"ลงทะเบียน", r"ขอนัด", r"นัดหมาย", r"appointment",
    ],
    "check_appointment": [
        r"ตรวจสอบนัด", r"เช็คนัด", r"นัดวันไหน", r"นัดกี่โมง",
        r"ผมนัด", r"หนูนัด", r"ดูนัด",
    ],
    "pet_status": [
        r"สถานะ", r"ยังอยู่ไหม", r"เป็นยังไง", r"อาการ", r"ดีขึ้นไหม",
        r"รักษาอยู่", r"admit", r"แอดมิท", r"นอนรพ",
    ],
    "emergency": [
        r"ฉุกเฉิน", r"ด่วน", r"urgent", r"emergency", r"หายใจไม่ออก",
        r"ชัก", r"หมดสติ", r"เลือดออก", r"กินยา", r"วิกฤต",
    ],
    "location": [
        r"ที่ตั้ง", r"อยู่ที่ไหน", r"แผนที่", r"เส้นทาง", r"location",
        r"address", r"ที่อยู่", r"สาขา",
    ],
    "price": [
        r"ราคา", r"ค่าใช้จ่าย", r"ค่าตรวจ", r"เท่าไร", r"เท่าไหร่",
        r"กี่บาท", r"แพงไหม", r"ค่าบริการ",
    ],
    "services": [
        r"บริการ", r"ทำอะไรบ้าง", r"มีอะไรบ้าง", r"service",
        r"รักษา", r"ผ่าตัด", r"เอ็กซเรย์", r"lab",
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
    n = bkk_now()
    return f"{n.day} {THAI_MONTHS[n.month]} {n.year+543}"


def get_today_appointments() -> list:
    data = get_data()
    appts = data.get("appointments", [])
    today = bkk_now().strftime("%Y-%m-%d")
    return [a for a in appts if a.get("date", "") == today]


def get_vets() -> list:
    data = get_data()
    return data.get("vets", [])


GREETING_TEXT = (
    "สวัสดีค่ะ รพส.หมาแมวเลิฟลี่ ยินดีให้บริการค่ะ 🐾\n"
    "เปิดทุกวัน 8:30-20:30น.\n\n"
    f"📞 080-4288181 (สาขาถนนราชวิถี)\n"
    f"📞 090-1556446 (สาขาหลังม.ศิลปากร)\n\n"
    "📍 Location สาขา1 (ถนนราชวิถี)\n"
    "https://maps.app.goo.gl/HcZtm5Hq2EDbX2YNA\n\n"
    "📍 Location สาขา2 (หลังม.ศิลปากร)\n"
    "https://maps.app.goo.gl/LhX7jBXETBDcw3jC9\n\n"
    "ต้องการสอบถามเรื่องอะไรเพิ่มเติมคะ"
)


def absolute_image_url(rel_path: str) -> str:
    """แปลง /static/images/xxx.jpg -> https://host.com/static/images/xxx.jpg
    พร้อม URL-encode ชื่อไฟล์ภาษาไทย เพื่อให้ LINE API ยอมรับ"""
    from urllib.parse import quote
    if rel_path.startswith("http"):
        return rel_path
    # encode เฉพาะชื่อไฟล์ภาษาไทย (ไม่ encode /)
    encoded = "/".join(quote(part, safe="") for part in rel_path.split("/"))
    return PUBLIC_BASE_URL + encoded


def build_response(intent: str, user_msg: str = ""):
    """
    สร้างข้อความตอบกลับตาม intent
    คืน dict: {"text": str, "images": [absolute_url, ...]}
    """
    if intent == "greeting":
        return {"text": GREETING_TEXT, "images": []}

    rep = _get_real_reply(intent)
    return {
        "text":   rep["text"],
        "images": [absolute_image_url(p) for p in rep.get("images", [])],
    }


def build_response_text(intent: str, user_msg: str = "") -> str:
    """Backward-compat: คืนเฉพาะ text สำหรับ endpoint /test/"""
    return build_response(intent, user_msg)["text"]


# Legacy code path — เก็บไว้เผื่อ debug แต่ไม่ใช้แล้ว
def _legacy_build_response(intent: str, user_msg: str = "") -> str:
    """สร้างข้อความตอบกลับตาม intent (legacy — ใช้ build_response)"""

    if intent == "greeting":
        return GREETING_TEXT

    elif intent == "hours":
        return (
            "🕐 เวลาทำการ Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📅 เปิดทุกวัน (ไม่มีวันหยุด)\n"
            "   ⏰ 08:30 – 20:30 น.\n\n"
            "📍 สาขาราชวิถี (นครปฐม)\n"
            f"   📞 {CLINIC_PHONE}\n\n"
            "📍 สาขาหลังม.ศิลปากร\n"
            f"   📞 {CLINIC_PHONE2}\n\n"
            "⚠️ วันหยุดนักขัตฤกษ์อาจมีการปรับเวลา\n"
            "   โปรดโทรสอบถามล่วงหน้าครับ"
        )

    elif intent == "neuter":
        return (
            "✂️ ราคาทำหมัน Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🐶 สุนัข:\n"
            "  น้ำหนัก < 10 กก.\n"
            "    ตัวเมีย ฿2,500 | ตัวผู้ ฿1,800\n"
            "  น้ำหนัก 10–20 กก.\n"
            "    ตัวเมีย ฿3,000 | ตัวผู้ ฿2,000\n"
            "  น้ำหนัก 20–30 กก.\n"
            "    ตัวเมีย ฿4,000 | ตัวผู้ ฿2,500\n\n"
            "🐱 แมว (ตัวผู้):\n"
            "  📦 รับกลับบ้านได้เลย ฿1,200\n"
            "     (ผ่าตัด 900 + เลือด 200 + ปลอกคอ 100)\n"
            "  🏥 พักฟื้นจนตัดไหม ฿2,200\n\n"
            "ℹ️ ราคาอาจแตกต่างตามน้ำหนักและสุขภาพ\n"
            f"📞 สอบถาม/นัดทำหมัน: {CLINIC_PHONE}"
        )

    elif intent == "flea":
        return (
            "🦟 ยากำจัดเห็บหมัด\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🐶 สำหรับสุนัข:\n"
            "  💊 Bravecto      ฿750–1,000 / 3 เดือน\n"
            "  💊 Nexgard Spectra ฿350–560 / เดือน\n\n"
            "🐱 สำหรับแมว:\n"
            "  💊 Advocate      ฿250–300 / เดือน\n"
            "  💊 Bravecto Plus  ฿700–800 / 3 เดือน\n\n"
            "ℹ️ ราคาแตกต่างตามน้ำหนักของสัตว์\n"
            f"📞 สอบถามเพิ่มเติม: {CLINIC_PHONE}"
        )

    elif intent == "boarding":
        return (
            "🏠 บริการฝากเลี้ยง\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🐾 ราคาฝากเลี้ยง: ฿200 / คืน\n\n"
            "📋 สิ่งที่ต้องเตรียม:\n"
            "• ประวัติวัคซีน (ถ้ามี)\n"
            "• อาหารที่น้องกินประจำ\n"
            "• ยาประจำ (ถ้ามี)\n"
            "• ของเล่นหรือผ้าห่มที่คุ้นเคย\n\n"
            f"📞 สำรองที่: {CLINIC_PHONE}\n"
            f"📞 หรือ: {CLINIC_PHONE2}"
        )

    elif intent == "price":
        return (
            "💰 ราคาค่าบริการ\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🩺 ค่าตรวจ (สัตวแพทย์)  ฿100\n"
            "🧪 เจาะเลือด CBC         ฿200\n"
            "🔬 เคมีเลือด             ฿150 / ค่า\n"
            "📷 Ultrasound            ฿300\n"
            "📷 X-ray                 ฿400\n"
            "🏠 ฝากเลี้ยง             ฿200 / คืน\n\n"
            "✂️ ทำหมัน — พิมพ์ 'ทำหมัน' เพื่อดูราคา\n"
            "💉 วัคซีน  — พิมพ์ 'วัคซีน' เพื่อดูราคา\n"
            "🦟 ยาเห็บหมัด — พิมพ์ 'เห็บ' เพื่อดูราคา\n\n"
            f"📞 สอบถามเพิ่มเติม: {CLINIC_PHONE}"
        )

    elif intent == "appointment":
        today_appts = get_today_appointments()
        appt_info = f"วันนี้มีนัดแล้ว {len(today_appts)} ราย" if today_appts else "วันนี้ยังมีเวลาว่าง"
        return (
            "📅 นัดหมายกับ Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📌 สถานะวันนี้ ({thai_date_short()}): {appt_info}\n\n"
            "📞 สาขาราชวิถี:\n"
            f"   โทร {CLINIC_PHONE}\n\n"
            "📞 สาขาหลังม.ศิลปากร:\n"
            f"   โทร {CLINIC_PHONE2}\n\n"
            f"💬 LINE OA: {CLINIC_LINE_OA}\n"
            f"   {CLINIC_LINE_URL}\n\n"
            "⏰ รับโทรศัพท์ 08:30–20:30 น. ทุกวัน\n\n"
            "กรุณาแจ้ง:\n"
            "• ชื่อเจ้าของและชื่อสัตว์\n"
            "• ประเภทสัตว์ (สุนัข/แมว/อื่นๆ)\n"
            "• อาการเบื้องต้น"
        )

    elif intent == "check_appointment":
        return (
            "🔍 ตรวจสอบนัดหมาย\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "กรุณาแจ้งข้อมูลใดข้อหนึ่ง:\n"
            "• HN (หมายเลขผู้ป่วย)\n"
            "• ชื่อสัตว์เลี้ยง + ชื่อเจ้าของ\n\n"
            "📞 โทรตรวจสอบ:\n"
            f"   สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"   สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}"
        )

    elif intent == "pet_status":
        return (
            "🏥 ตรวจสอบสถานะสัตว์ที่รักษา\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "กรุณาแจ้งข้อมูล:\n"
            "• HN (หมายเลขผู้ป่วย)\n"
            "• ชื่อสัตว์เลี้ยง\n\n"
            "🔒 ข้อมูลสุขภาพเป็นความลับ\n"
            "เราจะยืนยันตัวตนก่อนให้ข้อมูลนะครับ\n\n"
            f"📞 สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"📞 สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}\n"
            "⏰ 08:30–20:30 น. ทุกวัน"
        )

    elif intent == "vaccine":
        return (
            "💉 ราคาวัคซีน Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🐶 สุนัข (ลูกหมาปีแรก):\n"
            "  • วัคซีนรวม 5 โรค  ฿250/เข็ม × 3 เข็ม\n"
            "  • พิษสุนัขบ้า      ฿100/เข็ม\n"
            "  • วัคซีนประจำปี    ฿350\n\n"
            "🐱 แมว:\n"
            "  • ไข้หัดหวัด (FVRCP) ฿300/เข็ม × 3 เข็ม\n"
            "  • พิษสุนัขบ้า       ฿100\n"
            "  • ลิวคีเมีย (FeLV)   ฿400 × 2 เข็ม\n\n"
            "📋 กำหนดการฉีด:\n"
            "  • ลูกสุนัข/แมว เริ่มที่อายุ 6–8 สัปดาห์\n"
            "  • กระตุ้นทุกปี\n\n"
            f"📞 นัดฉีดวัคซีน: {CLINIC_PHONE}"
        )

    elif intent == "emergency":
        return (
            "🚨 กรณีฉุกเฉิน — โทรด่วน!\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📞 สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"📞 สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}\n\n"
            "⚡ สัญญาณฉุกเฉินที่ต้องมาด่วน:\n"
            "• หายใจลำบาก / หอบ\n"
            "• ชัก / หมดสติ\n"
            "• เลือดออกมาก\n"
            "• กินสารพิษหรือยา\n"
            "• ปัสสาวะไม่ออกนานกว่า 12 ชม.\n"
            "• อาเจียน / ท้องเสียรุนแรง\n\n"
            "❗ อย่ารอ — มาโรงพยาบาลได้เลยครับ\n"
            "⏰ รับ 08:30–20:30 น. ทุกวัน"
        )

    elif intent == "location":
        return (
            "📍 ที่ตั้ง Dog and Cat Lovely\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🏥 สาขา 1 — ถนนราชวิถี\n"
            "   139/6 ถ.ราชวิถีพระปฐมเจดีย์\n"
            "   เมือง นครปฐม 73000\n"
            f"   📞 {CLINIC_PHONE}\n"
            f"   🗺️ {CLINIC_MAP1}\n\n"
            "🏥 สาขา 2 — หลังมหาวิทยาลัยศิลปากร\n"
            f"   📞 {CLINIC_PHONE2}\n"
            f"   🗺️ {CLINIC_MAP2}\n\n"
            "⏰ เปิดทุกวัน 08:30–20:30 น."
        )

    elif intent == "services":
        return (
            "🏥 บริการของเรา\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🩺 ตรวจรักษาทั่วไป\n"
            "💉 ฉีดวัคซีน\n"
            "🔬 ตรวจ Lab / เจาะเลือด / CBC\n"
            "📷 เอ็กซเรย์ / อัลตราซาวด์\n"
            "🏥 Admit / ค้างคืน\n"
            "✂️ ผ่าตัด / ทำหมัน\n"
            "🛁 อาบน้ำ-ตัดขน*\n"
            "💊 จำหน่ายยา\n"
            "🦟 ยากำจัดเห็บหมัด\n"
            "🏠 ฝากเลี้ยง (฿200/คืน)\n"
            "📋 ตรวจสุขภาพประจำปี\n\n"
            "* อาบน้ำแมว: สาขา 2 หลังม.ศิลปากร เท่านั้น\n\n"
            f"📞 สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"📞 สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}"
        )

    elif intent == "staff":
        vets = get_vets()
        if vets:
            vet_list = "\n".join(f"  • {v.get('name', v)}" for v in vets[:5])
            return (
                "👩‍⚕️ ทีมคุณหมอของเรา\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"{vet_list}\n\n"
                f"📞 นัดพบสัตวแพทย์: {CLINIC_PHONE}"
            )
        return (
            "👩‍⚕️ ทีมคุณหมอผู้เชี่ยวชาญ\n"
            "พร้อมดูแลสัตว์เลี้ยงของคุณด้วยความใส่ใจ 🐾\n\n"
            f"📞 สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"📞 สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}"
        )

    elif intent == "farewell":
        return (
            "😊 ขอบคุณที่ติดต่อมาครับ!\n"
            "🐾 ดูแลน้องให้ดีนะครับ\n\n"
            f"📞 สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"📞 สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}\n"
            f"💬 LINE: {CLINIC_LINE_OA}\n\n"
            "🏥 Dog and Cat Lovely Pet Hospital"
        )

    else:  # unknown
        return (
            "🤔 ขออภัยครับ ไม่แน่ใจว่าต้องการสอบถามเรื่องอะไร\n\n"
            "ลองพิมพ์คำเหล่านี้ได้ครับ:\n"
            "• \"เวลาเปิด\" — เวลาทำการ\n"
            "• \"ราคา\" — ค่าบริการทั่วไป\n"
            "• \"ทำหมัน\" — ราคาทำหมัน\n"
            "• \"วัคซีน\" — ราคาวัคซีน\n"
            "• \"เห็บ\" — ยากำจัดเห็บหมัด\n"
            "• \"ฝากเลี้ยง\" — บริการฝากสัตว์\n"
            "• \"ที่ตั้ง\" — แผนที่และเส้นทาง\n"
            "• \"นัดหมาย\" — จองคิว\n"
            "• \"ฉุกเฉิน\" — โทรด่วน\n\n"
            f"📞 สาขาราชวิถี: {CLINIC_PHONE}\n"
            f"📞 สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}\n"
            "⏰ 08:30–20:30 น. ทุกวัน"
        )


# ──────────────────────────────────────────────
#  LINE FLEX MESSAGE (สำหรับตอบกลับสวยงาม)
# ──────────────────────────────────────────────
def build_flex_greeting() -> dict:
    """Flex Message หน้าแรก — แสดงข้อความต้อนรับและเมนูหลัก"""
    return {
        "type": "flex",
        "altText": "สวัสดีค่ะ รพส.หมาแมวเลิฟลี่ ยินดีให้บริการค่ะ",
        "contents": {
            "type": "bubble",
            "size": "mega",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#FF8C00",
                "paddingAll": "20px",
                "contents": [
                    {"type": "text", "text": "🐾 รพส.หมาแมวเลิฟลี่",
                     "color": "#FFFFFF", "size": "xl", "weight": "bold"},
                    {"type": "text", "text": "ยินดีให้บริการค่ะ",
                     "color": "#FFE0B2", "size": "sm"},
                    {"type": "text", "text": "⏰ เปิดทุกวัน 8:30–20:30 น.",
                     "color": "#FFFFFF", "size": "sm", "margin": "md", "weight": "bold"},
                ]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": "📞 ติดต่อสาขา",
                     "weight": "bold", "size": "md", "color": "#FF8C00"},
                    {"type": "text", "text": f"สาขาถนนราชวิถี: {CLINIC_PHONE}",
                     "size": "sm", "wrap": True},
                    {"type": "text", "text": f"สาขาหลังม.ศิลปากร: {CLINIC_PHONE2}",
                     "size": "sm", "wrap": True},
                    {"type": "separator", "margin": "md"},
                    {"type": "text", "text": "ต้องการสอบถามเรื่องอะไรเพิ่มเติมคะ 😊",
                     "weight": "bold", "size": "sm", "margin": "md", "wrap": True},
                    _menu_row("🕐", "เวลาทำการ",     "8:30–20:30 น. ทุกวัน"),
                    _menu_row("💰", "ราคาค่าบริการ", "ตรวจ ทำหมัน วัคซีน เห็บหมัด"),
                    _menu_row("📅", "นัดหมาย",       "จองคิว ตรวจสอบนัด"),
                    _menu_row("🏠", "ฝากเลี้ยง",     "฿200 / คืน"),
                    _menu_row("📍", "ที่ตั้ง 2 สาขา", "แผนที่ Google Maps"),
                    _menu_row("🚨", "ฉุกเฉิน",       "โทรด่วน 24 ชม."),
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "action": {"type": "uri",
                                   "label": "📍 แผนที่ สาขาราชวิถี",
                                   "uri": CLINIC_MAP1},
                        "color": "#FF8C00",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "action": {"type": "uri",
                                   "label": "📍 แผนที่ สาขาหลังม.ศิลปากร",
                                   "uri": CLINIC_MAP2},
                        "color": "#FF6B00",
                        "style": "primary",
                    },
                    {
                        "type": "button",
                        "action": {"type": "uri",
                                   "label": f"📞 โทรสาขาราชวิถี",
                                   "uri": f"tel:{CLINIC_PHONE.replace('-','')}"},
                        "color": "#22c55e",
                        "style": "primary",
                    },
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


_last_line_error = {"status": None, "body": None, "payload": None}

def line_reply(reply_token: str, messages: list):
    """ส่ง reply กลับไปยัง LINE"""
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    payload = {"replyToken": reply_token, "messages": messages}
    r = req.post(LINE_REPLY_URL, headers=headers, json=payload, timeout=10)
    if r.status_code != 200:
        log.warning(f"LINE reply failed: {r.status_code} {r.text[:500]}")
        # เก็บ error ล่าสุดเพื่อ debug
        _last_line_error["status"] = r.status_code
        _last_line_error["body"] = r.text[:500]
        # เก็บ payload (ตัด text/replyToken ออกเพื่อความปลอดภัย)
        _last_line_error["payload"] = {
            "msg_count": len(messages),
            "msg_types": [m.get("type") for m in messages],
            "image_urls": [m.get("originalContentUrl") for m in messages if m.get("type") == "image"],
        }


def line_reply_text(reply_token: str, text: str):
    line_reply(reply_token, [{"type": "text", "text": text}])


def line_reply_flex(reply_token: str, flex: dict):
    line_reply(reply_token, [flex])


def verify_line_signature(body: bytes, signature: str) -> bool:
    """ยืนยัน LINE webhook signature"""
    if not LINE_CHANNEL_SECRET:
        log.error("[Security] LINE_CHANNEL_SECRET not set — rejecting webhook")
        return False
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
    """ส่งข้อความ text กลับไปยัง FB Messenger"""
    params  = {"access_token": FB_PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": recipient_id},
        "message":   {"text": text},
    }
    r = req.post(FB_SEND_URL, params=params, json=payload, timeout=10)
    if r.status_code != 200:
        log.warning(f"FB reply failed: {r.status_code} {r.text[:200]}")


def fb_send_image(recipient_id: str, image_url: str):
    """ส่งรูปไปยัง FB Messenger"""
    params  = {"access_token": FB_PAGE_ACCESS_TOKEN}
    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": image_url, "is_reusable": True},
            }
        },
    }
    r = req.post(FB_SEND_URL, params=params, json=payload, timeout=15)
    if r.status_code != 200:
        log.warning(f"FB image send failed: {r.status_code} {r.text[:200]}")


def fb_send_reply(recipient_id: str, reply: dict):
    """ส่งทั้งรูปและข้อความตามลำดับ (รูปก่อน แล้วข้อความ)"""
    for img_url in reply.get("images", []):
        fb_send_image(recipient_id, img_url)
    if reply.get("text"):
        fb_send_message(recipient_id, reply["text"])


def line_reply_with_images(reply_token: str, reply: dict):
    """ส่ง LINE: รูป (image messages) + ข้อความ (text message) ในครั้งเดียว
    LINE จำกัด 5 messages ต่อ reply"""
    msgs = []
    for img_url in reply.get("images", [])[:4]:  # max 4 รูป เพื่อให้เหลือ slot ให้ text
        msgs.append({
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl":    img_url,
        })
    if reply.get("text"):
        msgs.append({
            "type": "text",
            "text": reply["text"],
            "quickReply": {"items": quick_replies()},
        })
    if msgs:
        line_reply(reply_token, msgs)


# ──────────────────────────────────────────────
#  APPOINTMENT POSTBACK HANDLER (Phase D)
# ──────────────────────────────────────────────
def _notify_admin_appt(action: str, hn: str, pet: str, date: str, user_id: str):
    """แจ้ง admin ทาง LINE เมื่อลูกค้ายืนยัน/เลื่อนนัด"""
    admin_id = ADMIN_LINE_ID
    token    = LOVELY_BOT_TOKEN or LINE_CHANNEL_ACCESS_TOKEN
    if not admin_id or not token:
        return
    emoji  = "✅" if action == "confirm" else "⚠️"
    action_th = "ยืนยันนัด" if action == "confirm" else "ขอเลื่อนนัด"
    msg = (
        f"{emoji} ลูกค้า{action_th}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🐾 น้อง: {pet}\n"
        f"🆔 HN: {hn}\n"
        f"📅 วันนัด: {date}\n"
        f"👤 LINE ID: {user_id[:16]}..."
    )
    try:
        req.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"to": admin_id, "messages": [{"type": "text", "text": msg}]},
            timeout=5,
        )
    except Exception as e:
        log.warning(f"[admin notify] failed: {e}")


def _update_local_queue(qid: int, status: str, hn: str, pet: str, date: str):
    """เรียก local machine ผ่าน ngrok เพื่ออัพ Excel queue (best effort)"""
    import os as _os
    local_url = _os.environ.get("LOCAL_API_URL", "").rstrip("/")
    api_key   = _os.environ.get("INTERNAL_API_KEY", "dogcatlovely_internal_2026")
    if not local_url:
        return
    try:
        req.post(
            f"{local_url}/api/queue_response",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={"qid": qid, "status": status, "hn": hn, "pet": pet, "date": date},
            timeout=5,
        )
        log.info(f"[queue_response] updated qid={qid} → {status}")
    except Exception as e:
        log.warning(f"[queue_response] local update failed (ok): {e}")


def handle_appointment_postback(reply_token: str, user_id: str, data: str):
    """รับ postback จากปุ่ม Flex Message ของ appointment reminder
    Postback format: appt=confirm&qid=99&hn=690001-1&pet=หมูกะทะ&date=22+พ.ค.+2569
    """
    if not APPOINTMENT_DB_READY:
        log.warning("appointment_db not loaded — postback ignored")
        return

    # Parse query string format
    from urllib.parse import parse_qs
    try:
        params = parse_qs(data)
    except Exception:
        params = {}

    action = (params.get("appt") or [""])[0]
    qid    = (params.get("qid") or ["0"])[0]
    try:
        qid_int = int(qid)
    except ValueError:
        qid_int = 0

    if action not in ("confirm", "reschedule") or not qid_int:
        log.info(f"[{user_id}] postback not for appointment: {data}")
        return

    # ดึงข้อมูลจาก postback data ที่ฝังไว้ (ไม่ต้องเปิด xlsx)
    hn_pb   = (params.get("hn")   or [""])[0]
    pet_pb  = (params.get("pet")  or ["-"])[0].replace("+", " ")
    date_pb = (params.get("date") or [""])[0].replace("+", " ")

    # ลอง lookup local xlsx เพิ่มเติม (ถ้ามี) — ไม่ block ถ้าไม่เจอ
    appt = adb.find_queue_by_id(qid_int) or {}
    pet_name = appt.get("pet_name") or pet_pb or "-"
    hn_val   = appt.get("hn")       or hn_pb  or "-"
    thai_d   = date_pb  # ใช้จาก postback ก่อน
    if appt.get("appt_date"):
        try:
            from datetime import datetime as _dt
            THAI_M = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                      "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
            d = _dt.strptime(str(appt["appt_date"]), "%Y-%m-%d").date()
            thai_d = f"{d.day} {THAI_M[d.month]} {d.year + 543}"
        except Exception:
            pass

    # บันทึกลง Google Sheet ก่อน (persistent — ไม่หายตอน Railway redeploy)
    try:
        import gsheet_db
        if gsheet_db.is_enabled():
            gsheet_db.log_response(
                qid=qid_int, hn=hn_val, action=action,
                pet_name=pet_name, appt_date=thai_d, line_user_id=user_id,
            )
            gsheet_db.log_event(action.capitalize(), hn_val, user_id,
                                f"qid={qid_int} {pet_name} {thai_d}", "OK")
    except Exception as e:
        log.warning(f"[gsheet] log error: {e}")

    if action == "confirm":
        # อัพ xlsx ถ้ามีข้อมูล local
        if appt:
            adb.update_send_queue_status(qid_int, "Confirmed", "Confirmed via LINE")
            adb.log_event("Confirm", hn_val, user_id, f"qid={qid_int} {pet_name}", "OK")
        reply_text = (
            "✅ ยืนยันนัดเรียบร้อยค่ะ!\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            f"🐾 น้อง{pet_name}\n"
            f"📅 {thai_d}\n\n"
            "ขอบคุณค่ะ พบกันวันนัด 🐾💕\n"
            "หากต้องการเลื่อนนัด กรุณาโทร 090-1556446 ค่ะ"
        )
        line_reply_text(reply_token, reply_text)
        # แจ้ง admin + อัพ local xlsx ผ่าน ngrok
        _notify_admin_appt("confirm", hn_val, pet_name, thai_d, user_id)
        _update_local_queue(qid_int, "Confirmed", hn_val, pet_name, thai_d)

    elif action == "reschedule":
        if appt:
            adb.update_send_queue_status(qid_int, "Reschedule", "Customer requested reschedule")
            adb.log_event("Reschedule", hn_val, user_id, f"qid={qid_int} {pet_name}", "OK")
        reply_text = (
            "🕐 รับทราบค่ะ เจ้าหน้าที่จะติดต่อกลับเพื่อนัดใหม่\n"
            "━━━━━━━━━━━━━━━━━\n\n"
            f"🐾 น้อง{pet_name}\n"
            f"📅 นัดเดิม: {thai_d}\n\n"
            "หากต้องการนัดวันที่สะดวก กรุณาโทร:\n"
            "📞 090-1556446 ค่ะ\n\n"
            "ขออภัยในความไม่สะดวกค่ะ 🙏"
        )
        line_reply_text(reply_token, reply_text)
        # Railway ส่ง basic notification ทันที (fallback กรณีเครื่อง local ปิดอยู่)
        _notify_admin_appt("reschedule", hn_val, pet_name, thai_d, user_id)
        # local api_queue_response จะส่ง notification ฉบับเต็ม (+ เบอร์โทร) ถ้าเครื่องเปิดอยู่
        _update_local_queue(qid_int, "Reschedule", hn_val, pet_name, thai_d)


# ──────────────────────────────────────────────
#  QUICK REPLIES (LINE)
# ──────────────────────────────────────────────
def quick_replies() -> list:
    """Quick reply buttons สำหรับ LINE (max 13 items)"""
    items = [
        ("🕐 เวลาเปิด",    "เวลาเปิด"),
        ("📅 จองคิว",      "จองคิว"),
        ("✂️ ทำหมัน",      "ทำหมัน"),
        ("💉 วัคซีน",      "วัคซีน"),
        ("🦟 ยาเห็บ",      "ยากำจัดเห็บหมัด"),
        ("🏠 ฝากเลี้ยง",   "ฝากเลี้ยง"),
        ("📍 ที่ตั้ง",      "ที่ตั้งสาขา"),
        ("🚨 ฉุกเฉิน",     "ฉุกเฉิน"),
        ("🔄 เริ่มใหม่",   "สวัสดี"),
    ]
    return [
        {"type": "action", "action": {"type": "message", "label": label, "text": text}}
        for label, text in items
    ]


# ──────────────────────────────────────────────
#  ADMIN NOTIFICATION — แจ้งเตือนเมื่อบอทตอบไม่ได้
# ──────────────────────────────────────────────
_notified_recently: dict = {}   # user_id → timestamp (cooldown 10 นาที)
NOTIFY_COOLDOWN_SEC = 600       # ไม่ส่ง noti ซ้ำภายใน 10 นาที/user


def notify_admin_unanswered(user_id: str, user_text: str, draft: str = ""):
    """
    ส่ง push LINE ไปหา admin (Wirote) เมื่อบอทตอบไม่ได้
    พร้อมแนบ draft คำตอบที่ AI แนะนำ

    NOTE: ตั้งแต่ 2026-05-30 — ปิดไว้เป็น default เพื่อไม่รบกวน Wirote
    ตั้ง env NOTIFY_ADMIN_UNANSWERED=true ถ้าต้องการเปิดใช้
    """
    # Wirote ขอให้หยุดส่ง notify เมื่อบอทตอบไม่ได้ — บอทเงียบเฉยๆ ไม่แจ้งใคร
    if os.environ.get("NOTIFY_ADMIN_UNANSWERED", "false").lower() != "true":
        log.info(f"[notify] admin notification disabled — skip (user={user_id[-8:] if len(user_id)>8 else user_id})")
        return

    if not LOVELY_BOT_TOKEN or not ADMIN_LINE_ID:
        log.warning("[notify] LOVELY_BOT_TOKEN หรือ ADMIN_LINE_ID ยังไม่ได้ตั้งค่า")
        return

    # cooldown — ไม่ spam ถ้า user เดิมถามซ้ำเร็วๆ
    from datetime import datetime
    now_ts = datetime.now().timestamp()
    last = _notified_recently.get(user_id, 0)
    if now_ts - last < NOTIFY_COOLDOWN_SEC:
        log.info(f"[notify] cooldown active for {user_id} — skip")
        return
    _notified_recently[user_id] = now_ts

    # สร้างข้อความแจ้ง admin
    short_uid = user_id[-8:] if len(user_id) > 8 else user_id
    lines = [
        "🔔 บอทตอบไม่ได้ — รอการตอบกลับ",
        "━━━━━━━━━━━━━━━━━━",
        f"👤 User: ...{short_uid}",
        f"❓ คำถาม: {user_text}",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if draft:
        lines.append("💡 ตัวอย่างคำตอบ (AI draft):")
        lines.append(draft[:500])
        lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("📱 กรุณาตอบใน LINE OA Manager ค่ะ")

    msg = "\n".join(lines)

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {LOVELY_BOT_TOKEN}",
    }
    payload = {
        "to": ADMIN_LINE_ID,
        "messages": [{"type": "text", "text": msg}],
    }
    try:
        r = req.post(LINE_PUSH_URL, headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            log.info(f"[notify] admin notified for user={short_uid}")
        else:
            log.warning(f"[notify] push failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log.warning(f"[notify] push error: {e}")


# ──────────────────────────────────────────────
#  Q&A FLOW — Session state + multi-step
# ──────────────────────────────────────────────
_user_sessions: dict = {}

# ข้อความ header ที่แสดงก่อนทุก menu/response
BOT_INTRO_LINE = "สวัสดีค่ะ Lovely bot ขออนุญาตช่วยตอบคำถามเพื่อความรวดเร็วนะคะ 🐾"
BOT_INTRO_FB   = "รพส.หมาแมวเลิฟลี่ ยินดีให้บริการค่ะ"

def get_intro(platform: str = "line") -> str:
    """FB ใช้ intro text, LINE ไม่มี (มี OA greeting อยู่แล้ว)"""
    return BOT_INTRO_FB if platform == "fb" else ""

CONTACT_STAFF_MSG = (
    "🙇‍♀️ ขออภัยค่ะ คำถามนี้ขอติดต่อพนักงานให้นะคะ\n"
    "กรุณาโทร:\n"
    "📞 สาขาราชวิถี: 080-4288181\n"
    "📞 สาขาหลังม.ศิลปากร: 090-1556446\n"
    "⏰ รับโทรศัพท์ 8.30-20.30 น. ทุกวัน\n\n"
    "🤖 บอทขอหยุดตอบกลับชั่วคราว เจ้าหน้าที่จะติดต่อกลับโดยเร็วค่ะ"
)

NO_MATCH_MSG = (
    "🤔 ขออภัยค่ะ ไม่พบข้อมูลที่ตรงกับคำถามค่ะ\n\n"
    "ขอติดต่อพนักงานให้นะคะ:\n"
    "📞 สาขาราชวิถี: 080-4288181\n"
    "📞 สาขาหลังม.ศิลปากร: 090-1556446"
)


# ──────────────────────────────────────────────
#  HIERARCHICAL MENU TREE
# ──────────────────────────────────────────────
# item keys:
#   "label"  : ข้อความแสดงในเมนู
#   "next"   : menu_key ของ submenu ถัดไป
#   "qa_id"  : id ใน QA_LIST → แสดงคำตอบเลย
MENU_TREE: dict = {

    # ── ทำหมัน (root) ────────────────────────────
    "neuter_root": {
        "title": "ทำหมัน — เลือกประเภท",
        "items": [
            {"label": "🐶 ทำหมันสุนัขเพศผู้",  "next": "neuter_dog_weight"},
            {"label": "🐶 ทำหมันสุนัขเพศเมีย", "next": "neuter_dog_weight"},
            {"label": "🐱 ทำหมันแมวเพศผู้",    "next": "neuter_cat_male"},
            {"label": "🐱 ทำหมันแมวเพศเมีย",   "next": "neuter_cat_female"},
        ],
    },

    # ── ทำหมันสุนัข (เลือกน้ำหนัก) ─────────────
    "neuter_dog_weight": {
        "title": "ทำหมันสุนัข — เลือกน้ำหนัก",
        "items": [
            {"label": "⚖️ น้ำหนักไม่ถึง 10 กก.",  "qa_id": 46},
            {"label": "⚖️ น้ำหนัก 10-20 กก.",     "qa_id": 47},
            {"label": "⚖️ น้ำหนัก 20-30 กก.",     "qa_id": 45},
            {"label": "⚖️ น้ำหนัก 30-40 กก.",     "qa_id": 29},
        ],
    },

    # ── ทำหมันแมวเพศผู้ (เลือกแพ็กเกจ) ─────────
    "neuter_cat_male": {
        "title": "ทำหมันแมวเพศผู้ — เลือกแพ็กเกจ",
        "items": [
            {"label": "🏠 รับกลับบ้านได้เลย",          "qa_id": 18},
            {"label": "🏥 ฝากข้ามคืน (จนตัดไหม)",      "qa_id": 16},
        ],
    },

    # ── ทำหมันแมวเพศเมีย (เลือกแพ็กเกจ) ────────
    "neuter_cat_female": {
        "title": "ทำหมันแมวเพศเมีย — เลือกแพ็กเกจ",
        "items": [
            {"label": "🏠 รับกลับบ้านได้เลย",          "qa_id": 14},
            {"label": "🏥 ฝากข้ามคืน (จนตัดไหม)",      "qa_id": 13},
        ],
    },
}


def detect_menu_trigger(text: str) -> str | None:
    """
    ตรวจว่า user text ควร trigger menu tree ไหน
    คืน menu_key หรือ None (= ใช้ keyword search ปกติ)
    ลำดับสำคัญ: เฉพาะเจาะจงก่อน → generic ทีหลัง
    """
    # สุนัข + เพศผู้
    if re.search(r"ทำหมัน.*(สุนัข|หมา).*(เพศผู้|ตัวผู้)", text):
        return "neuter_dog_weight"
    # สุนัข + เพศเมีย
    if re.search(r"ทำหมัน.*(สุนัข|หมา).*(เพศเมีย|ตัวเมีย)", text):
        return "neuter_dog_weight"
    # แมว + เพศผู้
    if re.search(r"ทำหมัน.*แมว.*(เพศผู้|ตัวผู้)|ทำหมัน.*(เพศผู้|ตัวผู้).*แมว", text):
        return "neuter_cat_male"
    # แมว + เพศเมีย
    if re.search(r"ทำหมัน.*แมว.*(เพศเมีย|ตัวเมีย)|ทำหมัน.*(เพศเมีย|ตัวเมีย).*แมว", text):
        return "neuter_cat_female"
    # generic "ทำหมัน" / "ผ่าหมัน" — ไม่มีน้ำหนักระบุ
    if re.search(r"ทำหมัน|ผ่าหมัน", text):
        if not re.search(r"กก|กิโล|\d+\s*[-–]\s*\d+", text):   # ไม่มีน้ำหนัก
            return "neuter_root"
    return None


def build_menu_message(menu_key: str, platform: str = "line") -> str:
    """สร้างข้อความเมนู hierarchical"""
    node  = MENU_TREE[menu_key]
    intro = get_intro(platform)
    lines = []
    if intro:
        lines.append(f"{intro}\n")
    lines.append(f"📋 {node['title']}\n")
    for i, item in enumerate(node["items"], start=1):
        lines.append(f"{i}. {item['label']}")
    lines.append(f"\n{len(node['items'])+1}. ❌ ไม่ใช่ทุกข้อ ขอติดต่อพนักงาน")
    lines.append("\n💡 พิมพ์เลขข้อที่ต้องการค่ะ")
    return "\n".join(lines)


# ── Handoff cooldown ──
# เมื่อบอทตอบไม่ได้ → handoff → พนักงานต้องคุยต่อ
# บอทเงียบ X วินาที เพื่อไม่ขัดจังหวะ แล้ว auto-resume เพื่อให้ลูกค้ารายอื่นใช้บอทได้
HANDOFF_COOLDOWN_SEC = int(os.environ.get("HANDOFF_COOLDOWN_SEC", "43200"))  # default 12 ชม.

# ── Silent handoff ──
# True (default) → ถ้าคำถามไม่ชัดเจน บอทเงียบไม่ตอบเลย (notify admin เงียบๆ)
# False         → ตอบ "ติดต่อพนักงาน" เหมือนเดิม
SILENT_HANDOFF = os.environ.get("SILENT_HANDOFF", "true").lower() != "false"


def handoff_silently(sess: dict, user_id: str, user_text: str, draft: str = ""):
    """Helper: ตั้ง handoff cooldown + แจ้ง admin โดยไม่ตอบลูกค้า
    Return reply dict (CONTACT_STAFF_MSG) ถ้า SILENT_HANDOFF=False, None ถ้าเงียบ
    """
    sess["candidates"] = []
    set_handoff(sess)
    notify_admin_unanswered(user_id, user_text, draft)
    if SILENT_HANDOFF:
        log.info(f"[{user_id}] silent handoff — no reply to user")
        return None
    return {"text": CONTACT_STAFF_MSG, "images": []}


def get_session(user_id: str) -> dict:
    if user_id not in _user_sessions:
        _user_sessions[user_id] = {
            "candidates":    [],
            "handoff_until": 0,    # epoch timestamp; 0 = not handed off
            "menu_node":     None,
            "register_step": None, # None | "await_hn" — สำหรับ flow ลงทะเบียน
        }
    sess = _user_sessions[user_id]
    # backward compat — ถ้ามี handed_off boolean เก่า แปลงเป็น handoff_until
    if "handed_off" in sess:
        if sess.pop("handed_off") and not sess.get("handoff_until"):
            sess["handoff_until"] = datetime.now().timestamp() + HANDOFF_COOLDOWN_SEC
    sess.setdefault("handoff_until", 0)
    sess.setdefault("register_step", None)
    return sess


def reset_session(user_id: str):
    _user_sessions[user_id] = {
        "candidates":    [],
        "handoff_until": 0,
        "menu_node":     None,
        "register_step": None,
    }


def is_handed_off(sess: dict) -> bool:
    """True ถ้าอยู่ใน handoff cooldown — auto-expire หลัง 2 ชม."""
    until = float(sess.get("handoff_until", 0) or 0)
    if until <= 0:
        return False
    if datetime.now().timestamp() >= until:
        # cooldown หมดอายุ — auto-resume
        sess["handoff_until"] = 0
        sess["candidates"] = []
        return False
    return True


def set_handoff(sess: dict):
    """ตั้ง handoff cooldown 2 ชม."""
    sess["handoff_until"] = datetime.now().timestamp() + HANDOFF_COOLDOWN_SEC


def build_candidates_message(candidates: list, platform: str = "line") -> str:
    """สร้างข้อความให้ user เลือก — แสดง label สั้นๆ แทนคำถามเต็ม"""
    intro = get_intro(platform)
    lines = []
    if intro:
        lines.append(f"{intro}\n")
    lines.append("📋 ลูกค้าต้องการสอบถามบริการด้านไหนค่ะ\n")
    for i, item in enumerate(candidates, start=1):
        label = item.get("label") or item["question"]
        lines.append(f"{i}. {label}")
    lines.append(f"\n{len(candidates)+1}. ❌ ไม่ใช่ทุกข้อ ขอติดต่อพนักงาน")
    lines.append("\n💡 พิมพ์เลขข้อ เพื่อสอบถามรายละเอียดค่ะ")
    return "\n".join(lines)


def build_single_confirm_message(qa_item: dict) -> str:
    """ถ้าเจอข้อเดียว ขอ confirm — ใช้ label สั้นๆ"""
    label = qa_item.get("label") or qa_item["question"]
    return (
        f"❓ คุณต้องการสอบถามเรื่องนี้ใช่มั้ยคะ?\n\n"
        f"▶ {label}\n\n"
        f"💡 พิมพ์ \"ใช่\" เพื่อดูคำตอบ\n"
        f"    หรือพิมพ์ \"ไม่ใช่\" เพื่อติดต่อพนักงาน"
    )


AI_POLISH_ENABLED = os.environ.get("AI_POLISH_ENABLED", "true").lower() != "false"


def build_answer_reply(qa_item: dict) -> dict:
    """สร้าง reply dict {text, images} สำหรับคำตอบ
    ถ้า AI_POLISH_ENABLED → AI ปรับ tone ให้อ่อนโยนตาม persona น้องเลิฟลี่ (คงราคา/ตัวเลขเดิม)
    """
    base_answer = qa_item["answer"]
    if AI_POLISH_ENABLED:
        try:
            import ai_agent
            base_answer = ai_agent.polish_answer(
                qa_item["id"], qa_item["question"], base_answer
            )
        except Exception as e:
            log.warning(f"polish_answer error qa_id={qa_item.get('id')}: {e}")
    return {
        "text":   base_answer + FOOTER,
        "images": [absolute_image_url(p) for p in qa_item.get("images", [])],
    }


# ──────────────────────────────────────────────
#  Registration Flow — Phase A
#  ลูกค้าลงทะเบียนผ่านปุ่ม Rich Menu หรือพิมพ์ "ลงทะเบียน"
# ──────────────────────────────────────────────
REGISTER_TRIGGERS = [
    "ลงทะเบียน", "ลงทะเบียนรับนัด", "register", "/register",
    "สมัครรับนัด", "สมัคร", "ผูกบัญชี", "ผูก hn", "เชื่อม hn",
    "รับแจ้งนัด", "แจ้งนัด", "รับนัด", "ลงชื่อ",
]

# ── Rich Menu ปุ่มขวา: "ติดต่อคลินิก" ──
CONTACT_CLINIC_TRIGGERS = [
    "ติดต่อคลินิก", "ติดต่อ clinic", "contact",
    "เบอร์โทรคลินิก", "โทรหาคลินิก",
]

CONTACT_CLINIC_REPLY = (
    "📞 ติดต่อ Dog and Cat Lovely\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    f"🏥 สาขาราชวิถี (นครปฐม)\n"
    f"   📞 {CLINIC_PHONE}\n"
    f"   📍 139/6 ถ.ราชวิถีพระปฐมเจดีย์\n\n"
    f"🏥 สาขาหลังม.ศิลปากร\n"
    f"   📞 {CLINIC_PHONE2}\n\n"
    f"⏰ เปิดทุกวัน 08:30 – 20:30 น.\n\n"
    f"💬 LINE OA: @dogcatlovely\n"
    f"   {CLINIC_LINE_URL}"
)


def _is_register_trigger(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(trig.lower() in t for trig in REGISTER_TRIGGERS)


def _is_contact_clinic_trigger(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(trig.lower() in t for trig in CONTACT_CLINIC_TRIGGERS)


def handle_register_flow(sess: dict, user_id: str, user_text: str):
    """จัดการ flow ลงทะเบียน HN ↔ LINE userId
    Return:
      dict {text,images}   = ตอบกลับลูกค้า
      None                 = flow นี้ไม่ relevant — ให้ flow อื่นทำต่อ
    """
    if not APPOINTMENT_DB_READY:
        return None

    text = (user_text or "").strip()

    # State 1: user trigger ลงทะเบียน
    if _is_register_trigger(text):
        # ถ้าเคยลงทะเบียนแล้ว — แจ้ง
        existing = adb.find_customer_by_user_id(user_id)
        if existing and existing.get("hn"):
            sess["register_step"] = None
            return {
                "text": (
                    f"📌 คุณลงทะเบียนรับนัดไว้แล้วค่ะ\n"
                    f"  HN: {existing['hn']}\n"
                    f"  เจ้าของ: {existing.get('owner_name') or '-'}\n"
                    f"  น้อง: {existing.get('pet_name') or '-'}\n\n"
                    f"หากต้องการเปลี่ยน HN กรุณาพิมพ์ \"เปลี่ยน HN\" "
                    f"แล้วส่งเลข HN ใหม่ได้เลยค่ะ 🐾"
                ),
                "images": [],
            }
        sess["register_step"] = "await_hn"
        return {
            "text": (
                "📝 ลงทะเบียนรับการแจ้งนัดทาง LINE\n"
                "━━━━━━━━━━━━━━━━━\n\n"
                "กรุณาส่งเลข HN ของน้อง (ดูได้จากใบเสร็จหรือบัตรประจำตัวสัตว์เลี้ยง) "
                "ตัวอย่าง: 690200-1\n\n"
                "หรือพิมพ์ \"ยกเลิก\" เพื่อออกจากการลงทะเบียนค่ะ"
            ),
            "images": [],
        }

    # State 1.5: user ต้องการเปลี่ยน HN
    _change_hn_triggers = ["เปลี่ยน hn", "เปลี่ยนhn", "change hn", "เปลี่ยนเลข", "เปลี่ยน hน", "เปลี่ยน"]
    if any(trig in text.lower() for trig in _change_hn_triggers):
        sess["register_step"] = "await_hn"
        return {
            "text": (
                "🔄 เปลี่ยน HN ใหม่ค่ะ\n"
                "━━━━━━━━━━━━━━━━━\n\n"
                "กรุณาส่งเลข HN ใหม่ ตัวอย่าง: 690200-1\n\n"
                "หรือพิมพ์ \"ยกเลิก\" เพื่อออกค่ะ"
            ),
            "images": [],
        }

    # State 1.7: ลูกค้าส่ง HN มาตรงๆ โดยไม่ได้พิมพ์ "ลงทะเบียน" ก่อน
    #            (พฤติกรรมที่พบบ่อยที่สุด — ลูกค้าเห็นว่าต้องส่ง HN ก็ส่งเลย)
    #            HN pattern เจาะจง (\d{6,7}-\d+) ไม่ชนคำถาม Q&A → ปลอดภัย
    #            ตั้ง register_step แล้วให้ State 2 ด้านล่างประมวลผลต่อในรอบเดียว
    if sess.get("register_step") != "await_hn" and adb.normalize_hn(text):
        log.info(f"[Register] bare HN detected ({text!r}) — auto-enter registration")
        sess["register_step"] = "await_hn"

    # State 2: awaiting HN
    if sess.get("register_step") == "await_hn":
        # ยกเลิก
        if text.lower() in ("ยกเลิก", "cancel", "exit", "ออก"):
            sess["register_step"] = None
            return {"text": "ยกเลิกการลงทะเบียนค่ะ 🐾", "images": []}

        hn = adb.normalize_hn(text)
        if not hn:
            # ถ้าข้อความไม่ดูเหมือนพยายามส่ง HN (ไม่มีตัวเลข/คำ HN)
            # → ออกจาก register flow แล้วให้ Q&A จัดการต่อตามปกติ
            # (ป้องกันบอทวนถาม HN ซ้ำเมื่อลูกค้าถามเรื่องอื่น)
            if not re.search(r'\d{5,}', text) and not re.search(r'\bhn\b', text.lower()):
                log.info(f"[Register] await_hn: non-HN message detected ({repr(text)[:40]}) — exit register flow → Q&A")
                sess["register_step"] = None
                return None  # fall through to Q&A
            return {
                "text": (
                    "🤔 ขออภัยค่ะ ไม่พบเลข HN ในข้อความ\n"
                    "กรุณาส่งเป็นรูปแบบ เช่น \"690200-1\" หรือ \"HN 690200-1\"\n\n"
                    "หรือพิมพ์ \"ยกเลิก\" เพื่อออกจากการลงทะเบียนค่ะ"
                ),
                "images": [],
            }

        # verify ใน DRX (optional — ถ้าไม่มี drx_data.json ให้รับ HN ได้เลย)
        info = adb.verify_hn_with_drx(hn)
        drx_available = (Path(__file__).parent / "drx_data.json").exists()

        if drx_available and not info:
            # HN ไม่พบใน DRX ตอนนี้ (อาจเป็นลูกค้าใหม่ที่ยังไม่มีข้อมูลเข้าระบบ)
            # → ลงทะเบียนไว้ก่อน ระบบจะ backfill ชื่อเมื่อ DRX sync ครั้งถัดไป
            info = {"_pending_drx_match": True}
            log.info(f"[Register] HN {hn} not in DRX yet — register pending, will backfill on next sync")

        # ไม่มีไฟล์ DRX (Railway) → รับ HN ได้เลย ไม่บล็อก
        if not info:
            info = {}

        # ตรวจ quota ก่อน save — สูงสุด MAX_USERS_PER_HN ต่อ HN
        existing_users = adb.find_customers_by_hn(hn)
        already_mine   = any(c.get("line_user_id") == user_id for c in existing_users)
        if not already_mine and len(existing_users) >= adb.MAX_USERS_PER_HN:
            sess["register_step"] = None
            return {
                "text": (
                    f"⚠️ HN {hn} ลงทะเบียนครบแล้วค่ะ\n"
                    f"(รับได้สูงสุด {adb.MAX_USERS_PER_HN} LINE ต่อ 1 HN)\n\n"
                    "หากต้องการแก้ไข กรุณาติดต่อคลินิก:\n"
                    "📞 080-4288181 ค่ะ"
                ),
                "images": [],
            }

        # save mapping
        result = adb.register_customer(
            line_user_id=user_id,
            hn=hn,
            owner_name=info.get("owner_name", ""),
            pet_name=info.get("pet_name", ""),
        )
        sess["register_step"] = None

        owner_txt = info.get("owner_name") or ""
        pet_txt   = info.get("pet_name")   or ""
        if owner_txt or pet_txt:
            detail = (
                f"  HN: {hn}\n"
                f"  เจ้าของ: {owner_txt or '-'}\n"
                f"  น้อง: {pet_txt or '-'}\n\n"
            )
        else:
            # ลูกค้าใหม่หรือ HN ยังไม่มีข้อมูลใน DRX
            detail = (
                f"  HN: {hn}\n\n"
                "📌 ระบบจะดึงชื่อเจ้าของและน้องจากฐานข้อมูลให้อัตโนมัติค่ะ\n\n"
            )

        return {
            "text": (
                "✅ ลงทะเบียนเรียบร้อยค่ะ!\n"
                "━━━━━━━━━━━━━━━━━\n\n"
                + detail +
                "🔔 ระบบจะส่งข้อความแจ้งเตือนนัดหมาย\n"
                "  ล่วงหน้า 2 วัน ก่อนถึงวันนัดค่ะ\n\n"
                "ขอบคุณที่ใช้บริการ Dog and Cat Lovely ค่ะ 🐾💕"
            ),
            "images": [],
        }

    # ไม่อยู่ใน register flow
    return None


def handle_qa_flow(user_id: str, user_text: str, platform: str = "line"):
    """
    Main Q&A flow handler — return dict {text, images} หรือ None ถ้าไม่ตอบ
    platform: "line" | "fb"
    Flow:
      0.5. registration (HN ↔ userId)
      1. greeting → reset + ตอบ
      2. menu_node active → navigate menu tree
      3. detect menu trigger → เปิด menu tree
      4. candidates active → เลือกข้อ / ยืนยัน
      5. keyword search → หา candidates หรือ handoff
    """
    sess = get_session(user_id)

    # ── 0.4 Rich Menu ปุ่มขวา: "ติดต่อคลินิก" — ทำงานเสมอ (ก่อน cooldown) ──
    if _is_contact_clinic_trigger(user_text):
        return {"text": CONTACT_CLINIC_REPLY, "images": []}

    # ── 0.5 Registration flow (HN ↔ LINE userId) — ต้องอยู่ "ก่อน" handoff cooldown! ──
    #    บั๊กเดิม: cooldown เช็คก่อน → ลูกค้าที่เคยส่งข้อความไม่ตรง (Claude ปิด → เข้า
    #    cooldown 12 ชม.) จะส่ง HN/ลงทะเบียนไม่ได้เลย บอทเงียบ 12 ชม. (เคส 690058-1)
    #    การลงทะเบียนเป็น action ที่ลูกค้าตั้งใจ → ต้องตอบเสมอ ไม่ติด cooldown
    register_reply = handle_register_flow(sess, user_id, user_text)
    if register_reply is not None:
        return register_reply

    # ── 0. Handoff cooldown → บอทเงียบ (ให้พนักงานคุย) สำหรับคำถาม Q&A อื่นๆ ──
    if is_handed_off(sess):
        remaining = int(sess["handoff_until"] - datetime.now().timestamp())
        log.info(f"[{user_id}] handoff cooldown {remaining}s remaining — skip")
        return None

    # ── 1. Greeting → reset ──
    intent = detect_intent(user_text)
    if intent == "greeting":
        reset_session(user_id)
        return {"text": GREETING_TEXT, "images": []}

    # ── 2. Menu tree navigation ──
    if sess.get("menu_node"):
        menu_key = sess["menu_node"]
        node     = MENU_TREE.get(menu_key)
        if node:
            choice = qa.parse_choice_number(user_text)
            if choice is not None:
                n = len(node["items"])
                if 1 <= choice <= n:
                    item = node["items"][choice - 1]
                    if "next" in item:
                        # ไปยัง submenu ถัดไป
                        next_key = item["next"]
                        sess["menu_node"] = next_key
                        log.info(f"[{user_id}] menu → {next_key}")
                        return {"text": build_menu_message(next_key, platform), "images": []}
                    elif "qa_id" in item:
                        # แสดงคำตอบ
                        qa_obj = qa.find_by_id(item["qa_id"])
                        reset_session(user_id)
                        log.info(f"[{user_id}] menu answer qa_id={item['qa_id']}")
                        if qa_obj:
                            return build_answer_reply(qa_obj)
                        return {"text": NO_MATCH_MSG, "images": []}
                elif choice == n + 1:
                    # ติดต่อพนักงาน — user ขอเอง (explicit) จึงตอบยืนยัน ไม่นับว่า "ไม่ชัดเจน"
                    return handoff_silently(sess, user_id, user_text)
                else:
                    return {"text": f"กรุณาเลือกเลข 1-{n+1} ค่ะ", "images": []}
            # ไม่ใช่ตัวเลข → reset menu แล้ว fall-through หาคำถามใหม่
            sess["menu_node"] = None

    # ── 2.5 Candidates fast-path — ถ้ามี candidates ค้างและ user ส่งเลข/ใช่/ไม่ใช่
    #         ให้ตอบจาก candidates โดยตรง (ไม่ผ่าน AI agent)
    #         ป้องกัน AI classify "2" หรือ "ใช่" เป็น handoff
    if sess.get("candidates"):
        cands = sess["candidates"]
        early_choice = qa.parse_choice_number(user_text)
        if early_choice is not None:
            if 1 <= early_choice <= len(cands):
                qa_obj = cands[early_choice - 1]
                reset_session(user_id)
                log.info(f"[{user_id}] candidates fast-path choice={early_choice} qa_id={qa_obj.get('id')}")
                return build_answer_reply(qa_obj)
            elif early_choice == len(cands) + 1:
                log.info(f"[{user_id}] candidates fast-path → handoff (chose last option)")
                return handoff_silently(sess, user_id, user_text)
            else:
                return {"text": f"ขออภัยค่ะ เลือกเลข 1-{len(cands)+1} นะคะ", "images": []}
        # ใช่ → ตอบ candidate แรก (single confirm flow)
        if qa.is_affirmative(user_text):
            qa_obj = cands[0]
            reset_session(user_id)
            log.info(f"[{user_id}] candidates fast-path affirm qa_id={qa_obj.get('id')}")
            return build_answer_reply(qa_obj)
        # ไม่ใช่ → handoff (เงียบ)
        if qa.is_negative(user_text):
            log.info(f"[{user_id}] candidates fast-path negative → handoff")
            return handoff_silently(sess, user_id, user_text)
        # อื่นๆ → fall through ไปค้นใหม่ (AI/keyword)

    # ── 2.7 Exact match → ตอบเลย ไม่ต้องเรียก AI (ประหยัด Anthropic API) ──
    # ปุ่ม / quick reply / copy-paste ส่งข้อความตรงกับคำถามใน KB เป๊ะ → ตอบจาก KB ตรงๆ
    # ปลอดภัย 100% (ตรงตัวอักษร ไม่เสี่ยงตอบผิด) และไม่เปลือง Claude call
    exact = qa.find_exact(user_text)
    if exact:
        reset_session(user_id)
        log.info(f"[{user_id}] exact match qa_id={exact['id']} — skip AI (ประหยัด API)")
        return build_answer_reply(exact)

    # ── 3. AI Agent (ลำดับแรก — ตอบทั้ง KB mode และ Free mode) ──
    try:
        import ai_agent
        ai_result = ai_agent.match_qa(user_text, qa.QA_LIST)
    except Exception as e:
        log.warning(f"[{user_id}] AI agent error: {e}")
        ai_result = None

    ai_draft = ""   # draft คำตอบสำหรับ admin
    if ai_result:
        ai_mode   = ai_result.get("mode", "kb")
        ai_conf   = ai_result["confidence"]
        qa_id     = ai_result["qa_id"]
        ai_answer = ai_result["answer"]
        ai_draft  = ai_result.get("draft", "")
        log.info(f"[{user_id}] AI mode={ai_mode} conf={ai_conf} qa_id={qa_id}")

        # ── 3a. Free mode — ปิดแล้ว: ไม่ตอบอิสระ ทุกคำถามต้องมีใน KB ──
        # (free mode ถูกลบออกจาก ai_agent.py — AI จะ classify เป็น handoff แทน)

        # ── 3b. KB mode + high confidence — ตอบจาก knowledge base ──
        if ai_mode == "kb" and ai_conf == "high" and qa_id:
            qa_obj = qa.find_by_id(qa_id)
            if qa_obj:
                reset_session(user_id)
                answer_text = ai_answer if ai_answer else qa_obj["answer"]
                return {
                    "text":   answer_text + FOOTER,
                    "images": [absolute_image_url(p) for p in qa_obj.get("images", [])],
                }

        # ── 3b.5 Partial mode — AI สังเคราะห์คำตอบจาก KB ที่ใกล้เคียง ──
        if ai_mode == "partial" and ai_answer:
            reset_session(user_id)
            log.info(f"[{user_id}] AI partial mode qa_id={qa_id} conf={ai_conf}")
            # แนบรูปจาก qa ที่ใกล้ที่สุด (ถ้ามี)
            qa_obj = qa.find_by_id(qa_id) if qa_id else None
            images = [absolute_image_url(p) for p in qa_obj.get("images", [])] if qa_obj else []
            return {
                "text":   ai_answer + FOOTER,
                "images": images,
            }

        # ── 3c. AI handoff — เงียบ ไม่ตอบลูกค้า ──
        if ai_mode == "handoff":
            reset_session(user_id)
            return handoff_silently(sess, user_id, user_text, ai_draft)

    # ── 4. Menu trigger detection (สำหรับคำสั้นๆ เช่น "ทำหมัน", "วัคซีน") ──
    menu_key = detect_menu_trigger(user_text)
    if menu_key:
        sess["menu_node"]  = menu_key
        sess["candidates"] = []
        log.info(f"[{user_id}] menu trigger → {menu_key}")
        return {"text": build_menu_message(menu_key, platform), "images": []}

    # ── 5. Candidates flow: user ไม่ได้ตอบเลข/ใช่/ไม่ใช่
    #         (step 2.5 จัดการกรณีตอบตรงแล้ว — ที่ตกถึงนี่คือ user พิมพ์อย่างอื่น)
    if sess.get("candidates"):
        # ค้นหาใหม่ — clear candidates เก่า
        sess["candidates"] = []

    # ── 6. KB medium confidence fallback ──
    if ai_result and ai_result.get("mode") == "kb" and ai_result["confidence"] == "medium":
        qa_obj = qa.find_by_id(ai_result["qa_id"]) if ai_result["qa_id"] else None
        if qa_obj:
            reset_session(user_id)
            answer_text = ai_result["answer"] if ai_result["answer"] else qa_obj["answer"]
            return {
                "text":   answer_text + FOOTER,
                "images": [absolute_image_url(p) for p in qa_obj.get("images", [])],
            }

    # ── 7. Last-resort: Keyword search ──
    candidates = qa.find_candidates(user_text, top_k=4)
    if not candidates:
        # ไม่เจอ keyword match → คำถามไม่ชัดเจน → เงียบ
        return handoff_silently(sess, user_id, user_text, ai_draft)

    if len(candidates) == 1:
        sess["candidates"] = candidates
        return {"text": build_single_confirm_message(candidates[0]), "images": []}

    sess["candidates"] = candidates
    return {"text": build_candidates_message(candidates, platform), "images": []}


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
            user_id   = (event.get("source") or {}).get("userId", "anonymous")
            log.info(f"LINE msg from {user_id}: {user_text!r}")
            _recent_senders.append({"user_id": user_id, "text": user_text})
            del _recent_senders[:-10]

            # ── Admin commands (Wirote ดึงข้อมูล OPD/สรุปยอดผ่านแชท) ──
            # เช็คก่อน Q&A flow เสมอ, gate ด้วย user_id == ADMIN_LINE_ID เท่านั้น
            # จึงไม่มีทางกระทบลูกค้า — ถ้าไม่ตรงคำสั่งใดๆ (คืน None) จะ fall-through
            # ไป handle_qa_flow ตามปกติ (เผื่อ Wirote พิมพ์ทดสอบ flow ลูกค้าเอง)
            if user_id == ADMIN_LINE_ID:
                try:
                    import bot_admin_commands
                    admin_reply = bot_admin_commands.handle_admin_command(user_text)
                except Exception as e:
                    log.exception(f"[admin cmd] error: {e}")
                    admin_reply = None
                if admin_reply is not None:
                    line_reply_text(reply_token, admin_reply["text"])
                    return

            # ผ่าน Q&A flow
            reply = handle_qa_flow(user_id, user_text)
            if reply is None:
                log.info(f"[{user_id}] bot silent (handed off)")
                return  # หยุดตอบกลับ
            line_reply_with_images(reply_token, reply)

    elif event_type == "follow":
        # ผู้ใช้ติดตาม LINE OA ใหม่ — ส่ง Flex greeting
        flex = build_flex_greeting()
        line_reply_flex(reply_token, flex)

    elif event_type == "postback":
        data = event.get("postback", {}).get("data", "")
        user_id = (event.get("source") or {}).get("userId", "")
        log.info(f"[{user_id}] Postback: {data}")
        handle_appointment_postback(reply_token, user_id, data)


# ──────────────────────────────────────────────
#  ROUTES — FACEBOOK MESSENGER WEBHOOK (ปิดชั่วคราว)
# ──────────────────────────────────────────────
FB_ENABLED = os.environ.get("FB_ENABLED", "false").lower() == "true"

@app.route("/webhook/fb", methods=["GET"])
def fb_verify():
    """Facebook webhook verification — ปิดชั่วคราว"""
    if not FB_ENABLED:
        return "FB webhook disabled", 503
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == FB_VERIFY_TOKEN:
        log.info("Facebook webhook verified!")
        return challenge, 200
    abort(403)


@app.route("/webhook/fb", methods=["POST"])
def fb_webhook():
    if not FB_ENABLED:
        return "ok", 200   # รับ request แต่ไม่ประมวลผล
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

        # ผ่าน Q&A flow
        reply = handle_qa_flow(sender_id, user_text, platform="fb")
        if reply is None:
            log.info(f"[FB:{sender_id}] bot silent (handed off)")
            return  # หยุดตอบกลับ
        fb_send_reply(sender_id, reply)

    elif "postback" in event:
        payload = event["postback"].get("payload", "")
        log.info(f"FB postback: {payload}")
        reset_session(sender_id)  # reset เพื่อเริ่มใหม่
        reply = {"text": GREETING_TEXT, "images": []}
        fb_send_reply(sender_id, reply)


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


@app.route("/test_notify", methods=["GET"])
def test_notify():
    """ทดสอบส่ง push แจ้งเตือน admin — เช็ก LOVELY_BOT_TOKEN + LINE_TARGET_ID"""
    token  = LOVELY_BOT_TOKEN
    target = ADMIN_LINE_ID
    status = {
        "LOVELY_BOT_TOKEN": (token[:16] + "...") if token else "❌ EMPTY",
        "LINE_TARGET_ID":   (target[:16] + "...") if target else "❌ EMPTY",
    }
    if not token or not target:
        return jsonify({"ok": False, "config": status, "error": "Missing env vars"}), 500

    # ล้าง cooldown ก่อนทดสอบ
    _notified_recently.pop("TEST_ADMIN", None)

    notify_admin_unanswered(
        user_id   = "TEST_ADMIN",
        user_text = "🧪 ทดสอบระบบแจ้งเตือน Railway — ถ้าเห็นข้อความนี้ระบบทำงานได้ครับ",
        draft     = "ตัวอย่างคำตอบ AI draft: สวัสดีค่ะ ขอบคุณที่สอบถามมาค่ะ ทางคลินิกจะติดต่อกลับโดยเร็วนะคะ 🐾",
    )
    return jsonify({"ok": True, "config": status, "msg": "Push sent — check your LINE"})




@app.route("/test_detect", methods=["GET"])
def test_detect():
    """ทดสอบการตรวจ intent จากข้อความ"""
    msg = request.args.get("msg", "")
    intent = detect_intent(msg)
    return jsonify({
        "message": msg,
        "intent":  intent,
        "reply":   build_response(intent, msg),
    })


@app.route("/test_qa", methods=["GET"])
def test_qa():
    """ทดสอบ Q&A flow"""
    msg      = request.args.get("msg", "")
    user_id  = request.args.get("user", "tester")
    platform = request.args.get("platform", "line")
    reply    = handle_qa_flow(user_id, msg, platform=platform)
    sess     = _user_sessions.get(user_id, {})
    return jsonify({
        "message":   msg,
        "user":      user_id,
        "reply":     reply,
        "session":   {
            "candidate_ids": [c["id"] for c in sess.get("candidates", [])],
            "handed_off":    is_handed_off(sess),
            "handoff_remaining_sec": max(0, int(sess.get("handoff_until", 0) - datetime.now().timestamp())) if sess.get("handoff_until") else 0,
        },
    })


@app.route("/test_reset", methods=["GET"])
def test_reset():
    """รีเซ็ต session ของ user"""
    user_id = request.args.get("user", "tester")
    reset_session(user_id)
    return jsonify({"status": "reset", "user": user_id})


@app.route("/test_admin_cmd", methods=["GET"])
def test_admin_cmd():
    """ทดสอบ admin command โดยตรงผ่าน HTTP (ไม่ต้องส่ง LINE จริง)
    ใช้ debug ได้สะดวกเพราะ log ของ process นี้ถูก buffer ไว้ (redirect ลงไฟล์) อ่านไม่ทันที
    เช่น: curl "http://localhost:5000/test_admin_cmd?msg=สรุปวันนี้" """
    msg = request.args.get("msg", "สรุปวันนี้")
    result = {"msg": msg}
    try:
        import bot_admin_commands
        result["admin_reply"] = bot_admin_commands.handle_admin_command(msg)
    except Exception as e:
        import traceback
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()[-800:]
    return jsonify(result)


@app.route("/api/admin_cmd", methods=["POST"])
def api_admin_cmd():
    """ให้ Railway ยิงกลับมาเครื่อง local ผ่าน ngrok เพื่อ query ข้อมูล OPD/สรุปยอด
    (Railway ไม่มี drx_opd.db ในเครื่องตัวเอง — ข้อมูลผู้ป่วยอยู่ที่เครื่องคลินิกที่เดียว)
    Header: X-API-Key: <INTERNAL_API_KEY>"""
    key = request.headers.get("X-API-Key", "")
    if key != INTERNAL_API_KEY:
        return jsonify({"error": "unauthorized"}), 403
    msg = (request.json or {}).get("msg", "")
    try:
        import bot_admin_commands
        reply = bot_admin_commands.handle_admin_command(msg, allow_remote=False)
    except Exception as e:
        import traceback
        log.exception(f"[api_admin_cmd] error: {e}")
        return jsonify({"error": str(e), "traceback": traceback.format_exc()[-800:]}), 500
    return jsonify({"reply": reply})


@app.route("/api/recent_senders", methods=["GET"])
def api_recent_senders():
    """ชั่วคราว: ดู userId ล่าสุดที่ทักบอทเข้ามา ใช้หา LINE_TARGET_ID จริงตอนเปลี่ยน channel
    Header: X-API-Key: <INTERNAL_API_KEY>"""
    key = request.headers.get("X-API-Key", "")
    if key != INTERNAL_API_KEY:
        return jsonify({"error": "unauthorized"}), 403
    return jsonify({"recent_senders": _recent_senders})


# ──────────────────────────────────────────────
#  START SCHEDULER (Daily Summary)
#  รันเฉพาะบนเครื่อง Local PC เท่านั้น
#  Railway ไม่รัน scheduler (ป้องกันส่ง LINE ซ้ำ 2 ครั้ง)
# ──────────────────────────────────────────────
_scheduler_instance = None
_is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))
if _is_railway:
    log.info("Railway environment detected — scheduler DISABLED (summary sent from local PC only)")
else:
    try:
        import scheduler as _scheduler_mod
        _scheduler_instance = _scheduler_mod.start_scheduler()
        if _scheduler_instance:
            log.info("Daily summary scheduler started (Local PC mode)")
        else:
            log.warning("Daily summary scheduler not started (missing deps?)")
    except Exception as _e:
        log.warning(f"Could not start scheduler: {_e}")


@app.route("/run_summary_now", methods=["GET"])
def run_summary_now():
    """ทดสอบรัน daily summary ทันที (manual trigger) — protected by INTERNAL_API_KEY"""
    key = request.headers.get("X-API-Key", request.args.get("key", ""))
    if key != INTERNAL_API_KEY:
        return jsonify({"error": "unauthorized"}), 403
    import traceback
    try:
        import scheduler as _s
        # ทำทีละขั้นเพื่อ debug
        import os as _os
        token = _os.environ.get("LOVELY_BOT_TOKEN", "").strip()
        target = _os.environ.get("LINE_TARGET_ID", "").strip()
        if not token:
            return jsonify({"step": "config", "error": "LOVELY_BOT_TOKEN missing"})
        if not target:
            return jsonify({"step": "config", "error": "LINE_TARGET_ID missing"})

        # ลอง fetch DRX
        try:
            import drx_bridge
            ok = drx_bridge.run_fetch()
            drx_status = "ok" if ok else "fetch_failed"
        except Exception as e:
            drx_status = f"exception: {e}"

        # ลองโหลด drx_data.json
        try:
            import line_sender
            data = line_sender.load_data()
            data_keys = list(data.keys())[:10]
        except SystemExit as e:
            return jsonify({"step": "load_data", "drx_status": drx_status,
                            "error": "drx_data.json not found (SystemExit)"})
        except Exception as e:
            return jsonify({"step": "load_data", "drx_status": drx_status,
                            "error": str(e), "trace": traceback.format_exc()[:1500]})

        # สร้างข้อความ
        from datetime import datetime as _dt
        now_hour = _dt.now().hour
        with_stock = request.args.get("stock") == "1" or now_hour == 20
        try:
            msg = line_sender.build_message(data)
        except Exception as e:
            return jsonify({"step": "build_message", "drx_status": drx_status,
                            "error": str(e), "trace": traceback.format_exc()[:1500]})

        # ส่ง LINE (main message)
        try:
            ok = line_sender.send_line_push(token, target, msg)
        except Exception as e:
            return jsonify({"step": "send_line", "drx_status": drx_status,
                            "msg_len": len(msg),
                            "error": str(e), "trace": traceback.format_exc()[:1500]})

        # ส่ง stock message แยก — รอบ 20:xx หรือ ?stock=1
        stock_sent = False
        if with_stock:
            try:
                stock_msg = line_sender.build_stock_message(data)
                line_sender.send_line_push(token, target, stock_msg)
                stock_sent = True
            except Exception as e:
                log.warning(f"[run_summary_now] stock send failed: {e}")

        return jsonify({"status": "ok", "drx_status": drx_status,
                        "msg_len": len(msg), "sent": ok, "stock_sent": stock_sent})
    except Exception as e:
        return jsonify({"status": "fatal", "error": str(e),
                        "trace": traceback.format_exc()[:2000]})


# ──────────────────────────────────────────────
#  API: sync customers ไปยัง local appointment_sync
# ──────────────────────────────────────────────
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "dogcatlovely_internal_2026")

@app.route("/api/test_drx", methods=["GET"])
def api_test_drx():
    """Debug: ทดสอบ DRX connection + login บน Railway"""
    key = request.headers.get("X-API-Key", "") or request.args.get("key", "")
    if key != INTERNAL_API_KEY:
        return jsonify({"error": "unauthorized"}), 403
    import traceback
    info = {}
    try:
        # 1. Check env vars
        info["drx_username_set"] = bool(os.environ.get("DRX_USERNAME", ""))
        info["drx_password_set"] = bool(os.environ.get("DRX_PASSWORD", ""))

        # 2. Network test - can we reach DRX URL?
        try:
            tr = req.get("http://dogcatlovely.thddns.net:8080/doctordogs/login", timeout=10)
            info["network_status"] = tr.status_code
            info["network_size"]   = len(tr.content)
        except Exception as ne:
            info["network_error"] = str(ne)

        # 3. Try login via drx_bridge
        try:
            import drx_bridge
            session = drx_bridge.make_session()
            ok = drx_bridge.login(session)
            info["login_ok"] = ok
        except Exception as le:
            info["login_error"] = str(le)[:200]
            info["login_trace"] = traceback.format_exc()[-500:]

        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()[-500:]}), 500


@app.route("/api/run/<job>", methods=["GET", "POST", "OPTIONS"])
def api_run_job(job: str):
    """Manual trigger สำหรับ scheduled jobs — protected by INTERNAL_API_KEY
    job: drx | queue | send
    """
    # CORS preflight + allow local dashboard HTML to call
    if request.method == "OPTIONS":
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        return resp

    key = request.headers.get("X-API-Key", "") or request.args.get("key", "")
    if key != INTERNAL_API_KEY:
        resp = jsonify({"error": "unauthorized"})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 403
    import io as _io
    import sys as _sys
    # Capture stdout to return it in response (debug)
    captured = _io.StringIO()
    old_stdout = _sys.stdout
    _sys.stdout = captured
    result = None
    try:
        if job == "queue":
            import appointment_sync
            appointment_sync.run_queue_build()
        elif job == "send":
            import appointment_sender
            old_argv = _sys.argv
            _sys.argv = ["appointment_sender.py"]
            try:
                result = appointment_sender.main()
            finally:
                _sys.argv = old_argv
        elif job == "drx":
            import appointment_sync
            appointment_sync.run_drx_sync(fetch_fresh=True)
        else:
            resp = jsonify({"error": f"unknown job: {job}"})
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp, 400
        resp = jsonify({
            "ok": True,
            "job": job,
            "stats": result,
            "output": captured.getvalue()[-2000:],   # ล่าสุด 2000 chars
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp
    except Exception as e:
        import traceback
        resp = jsonify({
            "error": str(e),
            "trace": traceback.format_exc()[-500:],
            "output": captured.getvalue()[-2000:],
        })
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp, 500
    finally:
        _sys.stdout = old_stdout


def _notify_reschedule_with_phone(hn: str, pet: str, date: str, qid: int):
    """ส่งสรุปขอเลื่อนนัดให้ Wirote — รวมชื่อเจ้าของ + เบอร์โทรจาก opd_db (รันบน local เท่านั้น)"""
    token = LOVELY_BOT_TOKEN or LINE_CHANNEL_ACCESS_TOKEN
    if not token or not ADMIN_LINE_ID:
        return
    owner = ""
    phone = ""
    try:
        import opd_db
        petid = hn.split("-")[0] if "-" in hn else hn
        with opd_db._connect() as conn:
            row = conn.execute(
                "SELECT c.full_name, c.tel FROM pets p "
                "JOIN customers c ON p.cuid = c.uid WHERE p.petid = ? LIMIT 1",
                (petid,),
            ).fetchone()
            if row:
                owner = row[0] or ""
                phone = row[1] or ""
    except Exception as e:
        log.warning(f"[reschedule notify] opd_db lookup failed: {e}")
    msg = (
        f"⚠️ ขอเลื่อนนัด\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🐾 น้อง{pet}\n"
        f"👤 เจ้าของ: {owner or '-'}\n"
        f"📅 นัดเดิม: {date}\n"
        f"🆔 HN: {hn}\n"
        f"📞 เบอร์: {phone or 'ไม่มีข้อมูล'}"
    )
    try:
        req.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"to": ADMIN_LINE_ID, "messages": [{"type": "text", "text": msg}]},
            timeout=10,
        )
        log.info(f"[reschedule notify] sent to admin for HN={hn}")
    except Exception as e:
        log.warning(f"[reschedule notify] push failed: {e}")


@app.route("/api/queue_response", methods=["POST"])
def api_queue_response():
    """รับ queue response จาก Railway (confirm/reschedule) → อัพ local Excel
    Header: X-API-Key: <INTERNAL_API_KEY>
    Body: {"qid": int, "status": "Confirmed"|"Reschedule", "hn": str, "pet": str, "date": str}
    """
    if not APPOINTMENT_DB_READY:
        return jsonify({"error": "appointment_db not ready"}), 503
    key = request.headers.get("X-API-Key", "")
    if key != INTERNAL_API_KEY:
        return jsonify({"error": "unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    qid    = data.get("qid")
    status = data.get("status")
    hn     = data.get("hn", "")
    pet    = data.get("pet", "")
    date   = data.get("date", "")
    if not qid or status not in ("Confirmed", "Reschedule"):
        return jsonify({"error": "invalid params"}), 400
    try:
        adb.update_send_queue_status(int(qid), status, f"{status} via LINE (Railway)")
        adb.log_event(status, hn, "", f"qid={qid} {pet} {date}", "OK")
        log.info(f"[queue_response] qid={qid} → {status} (hn={hn} pet={pet})")
        if status == "Reschedule":
            _notify_reschedule_with_phone(hn, pet, date, int(qid))
        return jsonify({"ok": True, "qid": qid, "status": status})
    except Exception as e:
        log.exception(f"[queue_response] error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/customers", methods=["GET"])
def api_customers():
    """คืน registered customers ทั้งหมด — ใช้โดย appointment_sync.py บน local
    Header: X-API-Key: <INTERNAL_API_KEY>
    """
    if not APPOINTMENT_DB_READY:
        return jsonify({"error": "appointment_db not ready"}), 503
    key = request.headers.get("X-API-Key", "")
    if key != INTERNAL_API_KEY:
        return jsonify({"error": "unauthorized"}), 403
    customers = adb.get_all_customers()
    # แปลง datetime objects เป็น string
    result = []
    for c in customers:
        result.append({k: str(v) if v is not None else None for k, v in c.items()})
    return jsonify({"customers": result, "count": len(result)})


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys, io
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print("=" * 50)
    print("  [*] Dog and Cat Lovely Chatbot Server")
    print("=" * 50)
    print(f"  LINE Token : {'[OK]' if LINE_CHANNEL_ACCESS_TOKEN else '[MISSING]'}")
    print(f"  FB Token   : {'[OK]' if FB_PAGE_ACCESS_TOKEN else '[MISSING]'}")
    print()
    print("  Endpoints:")
    print("    GET  /                  -> status")
    print("    POST /webhook/line      -> LINE webhook")
    print("    GET  /webhook/fb        -> FB verification")
    print("    POST /webhook/fb        -> FB messages")
    print("    GET  /test/<intent>     -> test response")
    print()
    print("  Test intents: greeting, hours, price, appointment,")
    print("                neuter, flea, boarding, vaccine,")
    print("                emergency, location, services")
    print()
    print(f"  http://0.0.0.0:{port}")
    print("=" * 50)

    app.run(host="0.0.0.0", port=port, debug=debug_mode)
