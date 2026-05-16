"""
LINE Daily Summary Sender v2
================================
ส่งสรุปประจำวัน 5 หัวข้อไปยัง LINE:
  1. รายรับสุทธิ
  2. รายละเอียดตามหมวดหมู่
  3. จำนวนเคสต่อวัน
  4. เคสค้างคืน (Admit)
  5. ยา/อุปกรณ์ใกล้หมดสต็อค

วิธีใช้:
  python line_sender.py              → แสดง preview + บันทึก line_summary.txt
  python line_sender.py --send       → ส่ง LINE จริง (ต้องตั้งค่า TOKEN ก่อน)
  python line_sender.py --fetch      → ดึงข้อมูลใหม่จาก DRX แล้วส่ง LINE

ขั้นตอนการขอ LINE Channel Access Token:
  1. ไปที่ https://developers.line.biz/console/
  2. สร้าง Provider → Messaging API channel
  3. Copy "Channel access token (long-lived)" ใส่ใน LINE_CHANNEL_ACCESS_TOKEN
  4. User ID: ไปที่ LINE OA Manager → Basic settings → Your user ID
     หรือดูจาก webhook event ตอน user ส่งข้อความมาครั้งแรก
"""

import json
import sys
import os
import io
import subprocess
import requests
from datetime import datetime
from pathlib import Path

# โหลด .env ถ้ามี (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# แก้ปัญหา emoji encoding ใน Windows terminal
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ============================================================
#  CONFIG — แก้ตรงนี้ก่อนใช้งานจริง
# ============================================================
# ใช้ LOVELY_BOT_TOKEN สำหรับ Lovely Bot (admin notifications)
# ถ้าไม่มี ใช้ LINE_TOKEN (default — สำหรับ local development)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get(
    "LOVELY_BOT_TOKEN",
    os.environ.get("LINE_TOKEN", "")
).strip()
LINE_TARGET_ID = os.environ.get(
    "LINE_TARGET_ID",
    "Ude09abe7b1f73ee901c047ccfe693dd8"   # ← Wirote (Dog and Cat Lovely)
).strip()
# LINE_TARGET_ID ที่ขึ้นต้นด้วย "U" = User, "C" = Group, "R" = Room

DATA_FILE    = Path(__file__).parent / "drx_data.json"
SUMMARY_FILE = Path(__file__).parent / "line_summary.txt"
BRIDGE_SCRIPT = Path(__file__).parent / "drx_bridge.py"

# ============================================================
#  THAI DATE HELPERS
# ============================================================
THAI_MONTHS = [
    "", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."
]

def thai_now() -> str:
    now = datetime.now()
    return f"{now.day} {THAI_MONTHS[now.month]} {now.year + 543} เวลา {now.hour:02d}:{now.minute:02d}"

def today_pattern() -> str:
    """ส่วนหัวของวันนี้ เช่น '15 พ.ค.' เพื่อ filter cases"""
    now = datetime.now()
    return f"{now.day} {THAI_MONTHS[now.month]}"


# ============================================================
#  DATA LOADING
# ============================================================
def load_data() -> dict:
    if not DATA_FILE.exists():
        print("❌ ไม่พบ drx_data.json — กรุณารัน drx_bridge.py ก่อน")
        sys.exit(1)
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    fetched = data.get("_meta", {}).get("fetched_at", "ไม่ทราบ")
    print(f"✅ โหลดข้อมูล (อัพเดท: {fetched})")
    return data


def fetch_fresh_data():
    """รัน drx_bridge.py เพื่อดึงข้อมูลใหม่จาก DRX"""
    print("🔄 กำลังดึงข้อมูลใหม่จาก DRX...")
    result = subprocess.run(
        [sys.executable, str(BRIDGE_SCRIPT)],
        capture_output=True, text=True, encoding="utf-8"
    )
    if result.returncode == 0:
        print("✅ ดึงข้อมูลสำเร็จ")
        return load_data()
    else:
        print(f"❌ drx_bridge.py ล้มเหลว:\n{result.stderr[:500]}")
        print("   ใช้ข้อมูลล่าสุดที่มีอยู่แทน...")
        return load_data()


# ============================================================
#  MESSAGE BUILDER
# ============================================================
CAT_ICONS = {
    "รายการยา":              "💊",
    "ค่าตรวจรักษา":          "🩺",
    "ค่า Lab":               "🧪",
    "ค่าบริการทางการแพทย์":  "💉",
    "สินค้า Pet Shop":        "🛍️",
    "อุปกรณ์และเวชภัณฑ์":    "🔧",
}

def fmt_baht(n) -> str:
    return f"฿{float(n):,.0f}"

def build_message(data: dict) -> str:
    dr     = data.get("daily_revenue", {})
    cases  = data.get("cases", [])
    stock  = data.get("stock", [])
    raw    = data.get("_raw", {})

    # ──────────────────────────────────────────────
    # 1. รายรับสุทธิ
    # ──────────────────────────────────────────────
    net_revenue    = float(dr.get("net_revenue", 0))
    received_total = float(dr.get("received_total", 0))
    bill_total     = float(dr.get("bill_total", 0))
    total_cost     = float(dr.get("total_cost", 0))
    bill_count     = int(dr.get("bill_count", 0))
    voided_count   = int(dr.get("voided_count", 0))
    cash           = float(dr.get("cash", 0))
    transfer       = float(dr.get("transfer", 0))      # โอนเงินค่าตรวจ
    deposit        = float(dr.get("deposit", 0))        # มัดจำทั้งหมด
    dep_transfer   = float(dr.get("deposit_transfer", 0))  # มัดจำผ่านโอน

    gross_margin = (net_revenue - total_cost) / net_revenue * 100 if net_revenue > 0 else 0

    # ──────────────────────────────────────────────
    # 2. หมวดหมู่
    # ──────────────────────────────────────────────
    categories = dr.get("categories", [])

    # ──────────────────────────────────────────────
    # 3. เคสวันนี้  — ใช้ date จาก daily_revenue เป็นหลัก
    # ──────────────────────────────────────────────
    # หา pattern วันที่จาก daily_revenue.date เช่น "14 พ.ค. 2569 เวลา 18:51"
    dr_date_str = dr.get("date", "")    # เช่น "14 พ.ค. 2569 เวลา 18:51"
    # ดึง "14 พ.ค." จาก date string นั้น
    import re as _re
    m = _re.search(r"(\d+\s+[ก-ฮ]+\.)", dr_date_str)
    report_day_pfx = m.group(1) if m else today_pattern()

    today_cases  = [c for c in cases if report_day_pfx in c.get("date", "")]
    done_cases   = [c for c in today_cases if c.get("status") == "done"]
    active_cases = [c for c in today_cases if c.get("status") == "active"]
    followup     = [c for c in today_cases if c.get("status") == "followup"]

    # ใช้ bill_count จาก daily_revenue เป็น fallback (แม่นกว่า)
    total_today = bill_count if bill_count > 0 else len(today_cases)

    # ──────────────────────────────────────────────
    # 4. เคสค้างคืน (Admit / Inpatient)
    # ──────────────────────────────────────────────
    # DRX ส่ง admit cases ผ่าน _raw.opd_active ถ้ามี
    opd_active_raw = raw.get("opd_active", []) or []
    admit_list = []
    for row in opd_active_raw:
        if isinstance(row, dict):
            cells = row.get("cell", [])
            if cells and len(cells) >= 3:
                admit_list.append({
                    "pet":   str(cells[1]).split("/")[0].strip() if "/" in str(cells[1]) else str(cells[1]),
                    "owner": str(cells[1]).split("/")[1].strip() if "/" in str(cells[1]) else "ไม่ระบุ",
                    "since": str(cells[0]),
                })

    # ──────────────────────────────────────────────
    # 5. สต็อกใกล้หมด / หมดแล้ว
    # ──────────────────────────────────────────────
    low_items = [s for s in stock if s.get("level") == "low"]
    out_items = [s for s in stock if s.get("level") == "out"]
    stock_tracked = any(s.get("qty", 0) > 0 for s in stock)  # ระบบติดตามสต็อกจริงไหม

    # ──────────────────────────────────────────────
    # BUILD MESSAGE
    # ──────────────────────────────────────────────
    SEP = "━" * 17

    # วันที่ในข้อความ: ถ้ามี daily_revenue.date ให้ใช้ของนั้น (วันที่รายงาน)
    report_date_display = dr_date_str if dr_date_str else thai_now()

    lines = [
        "📊 สรุปประจำวัน",
        "🏥 Dog and Cat Lovely",
        f"📅 {report_date_display}",
        SEP,
        "",
        "💰 รายรับสุทธิ",
        f"    {fmt_baht(net_revenue)} บาท",
        f"    (กำไรขั้นต้น {gross_margin:.1f}% / ต้นทุน {fmt_baht(total_cost)})",
        "",
        "📦 รายละเอียดตามหมวดหมู่",
    ]

    for cat in categories:
        val = float(cat.get("value", 0))
        if val > 0:
            label = cat.get("label", "")
            icon  = CAT_ICONS.get(label, "📌")
            lines.append(f"  {icon} {label}")
            lines.append(f"      {fmt_baht(val)} บาท")

    lines += [
        "",
        SEP,
        f"🏥 เคสวันนี้: {total_today} เคส",
        f"  ✅ เสร็จชำระแล้ว: {len(done_cases)} ราย",
        f"  🔄 อยู่ระหว่างรักษา: {len(active_cases)} ราย",
    ]
    if followup:
        lines.append(f"  📅 นัดติดตาม: {len(followup)} ราย")

    lines.append("")

    if admit_list:
        lines.append(f"🛏️ เคสค้างคืน (Admit): {len(admit_list)} ตัว")
        for a in admit_list[:5]:
            lines.append(f"  • {a['pet']} (เจ้าของ: {a['owner']})")
        if len(admit_list) > 5:
            lines.append(f"  ... และอีก {len(admit_list)-5} ตัว")
    else:
        lines.append("🛏️ เคสค้างคืน: ไม่มีคืนนี้")

    lines += [
        "",
        SEP,
        "💳 ช่องทางชำระเงิน",
        f"  💵 เงินสด:     {fmt_baht(cash)}",
        f"  📱 โอนเงิน:    {fmt_baht(transfer)}",
    ]
    if deposit > 0:
        lines.append(f"  🔒 รับมัดจำ:   {fmt_baht(deposit)}")
    lines += [
        f"  📃 จำนวนบิล:  {bill_count} ใบ",
    ]
    if voided_count > 0:
        lines.append(f"  🗑️ ยกเลิก:      {voided_count} ใบ")

    # Stock section
    lines += ["", SEP]
    if not stock_tracked:
        lines.append("📦 สต็อก: ระบบ DRX ไม่ได้บันทึกปริมาณ")
        lines.append("   (กรุณาตรวจสอบสต็อกในระบบโดยตรง)")
    elif low_items:
        lines.append(f"⚠️ ยา/อุปกรณ์ใกล้หมด ({len(low_items)} รายการ)")
        for s in low_items[:8]:
            unit = s.get("unit", "ชิ้น")
            lines.append(f"  • {s['name']}: เหลือ {s['qty']} {unit}")
        if len(low_items) > 8:
            lines.append(f"  ... และอีก {len(low_items)-8} รายการ")
    else:
        lines.append("✅ สต็อกปกติ ไม่มีรายการใกล้หมด")

    lines += ["", SEP, "🐾 Dog and Cat Lovely Pet Hospital"]

    return "\n".join(lines)


# ============================================================
#  LINE API
# ============================================================
def send_line_push(token: str, target_id: str, text: str) -> bool:
    """ส่ง push message ไปยัง LINE User/Group"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "to":       target_id,
        "messages": [{"type": "text", "text": text}],
    }
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code == 200:
        print("✅ ส่ง LINE สำเร็จ!")
        return True
    else:
        print(f"❌ ส่งไม่สำเร็จ: HTTP {r.status_code}")
        print(f"   Response: {r.text[:300]}")
        return False


def send_line_broadcast(token: str, text: str) -> bool:
    """Broadcast ไปยังผู้ติดตาม LINE OA ทั้งหมด (ต้องมี plan ที่รองรับ)"""
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {"messages": [{"type": "text", "text": text}]}
    r = requests.post(url, headers=headers, json=payload, timeout=15)
    if r.status_code == 200:
        print("✅ Broadcast LINE สำเร็จ!")
        return True
    else:
        print(f"❌ Broadcast ไม่สำเร็จ: HTTP {r.status_code} — {r.text[:200]}")
        return False


# ============================================================
#  MAIN
# ============================================================
def main():
    args = set(sys.argv[1:])
    fetch_new = "--fetch" in args
    do_send   = "--send"  in args
    broadcast = "--broadcast" in args

    print("=" * 45)
    print("  📊 LINE Daily Summary — Dog and Cat Lovely")
    print("=" * 45)

    # โหลดข้อมูล
    if fetch_new:
        data = fetch_fresh_data()
    else:
        data = load_data()

    # สร้างข้อความ
    msg = build_message(data)

    # บันทึก preview
    SUMMARY_FILE.write_text(msg, encoding="utf-8")

    # แสดง preview
    print()
    print("─" * 45)
    print(msg)
    print("─" * 45)
    print(f"\n📄 บันทึก preview → {SUMMARY_FILE.name}")

    # ส่ง LINE
    if not do_send and not broadcast:
        print("\nℹ️  เพิ่ม --send เพื่อส่ง LINE จริง")
        print("   เพิ่ม --fetch เพื่อดึงข้อมูลใหม่จาก DRX ก่อนส่ง")
        print("   เช่น: python line_sender.py --fetch --send")
        return

    if LINE_CHANNEL_ACCESS_TOKEN == "YOUR_LINE_CHANNEL_ACCESS_TOKEN":
        print("\n⚠️  ยังไม่ได้ตั้งค่า LINE_CHANNEL_ACCESS_TOKEN!")
        print("   ดูวิธีขอ token ด้านบนของไฟล์ line_sender.py")
        print("   หรือตั้ง environment variable: LINE_TOKEN=xxx")
        return

    print("\n📤 กำลังส่ง LINE...")

    if broadcast:
        send_line_broadcast(LINE_CHANNEL_ACCESS_TOKEN, msg)
    elif LINE_TARGET_ID == "YOUR_USER_ID_OR_GROUP_ID":
        print("⚠️  ยังไม่ได้ตั้งค่า LINE_TARGET_ID!")
        print("   ตั้ง environment variable: LINE_TARGET_ID=Uxxxxxxxxxx")
    else:
        send_line_push(LINE_CHANNEL_ACCESS_TOKEN, LINE_TARGET_ID, msg)


if __name__ == "__main__":
    main()
