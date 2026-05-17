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

def _parse_pet_owner(cell_str: str) -> tuple[str, str]:
    """แยก 'petname/ownername' → (pet, owner)"""
    s = str(cell_str or "").strip()
    if "/" in s:
        parts = s.split("/", 1)
        return parts[0].strip(), parts[1].strip()
    return s, "ไม่ระบุ"


def _extract_pet_list(raw_rows: list) -> list[dict]:
    """แปลง raw rows (op=5108) → [{time, pet, owner, hn, status, vet}]"""
    out = []
    for row in raw_rows or []:
        if not isinstance(row, dict):
            continue
        cells = row.get("cell", [])
        if len(cells) < 4:
            continue
        pet, owner = _parse_pet_owner(cells[1] if len(cells) > 1 else "")
        out.append({
            "time":   str(cells[0]) if cells else "",
            "pet":    pet,
            "owner":  owner,
            "hn":     str(cells[2]) if len(cells) > 2 else "",
            "status": str(cells[3]) if len(cells) > 3 else "",
            "vet":    str(cells[4]) if len(cells) > 4 else "",
        })
    return out


def build_message(data: dict) -> str:
    dr     = data.get("daily_revenue", {})
    stock  = data.get("stock", [])
    raw    = data.get("_raw", {})
    appts  = data.get("appointments", [])

    # ──────────────────────────────────────────────
    # 1. การเงิน — ใช้ received_total (รวมมัดจำ) ตามที่ DRX แสดง "ยอดรายรับสุทธิ รวม"
    # ──────────────────────────────────────────────
    received_total = float(dr.get("received_total", 0))   # 1,690 — รวมทุกช่องชำระ
    bill_total     = float(dr.get("bill_total", 0))       # 690 — ค่าบริการ (ไม่รวมมัดจำ)
    total_cost     = float(dr.get("total_cost", 0))
    bill_count     = int(dr.get("bill_count", 0))
    voided_count   = int(dr.get("voided_count", 0))
    cash           = float(dr.get("cash", 0))
    transfer       = float(dr.get("transfer", 0))         # โอนค่าบริการ
    credit         = float(dr.get("credit", 0))
    debit          = float(dr.get("debit", 0))
    deposit        = float(dr.get("deposit", 0))          # มัดจำเงินสด
    dep_transfer   = float(dr.get("deposit_transfer", 0)) # มัดจำผ่านโอน

    # กำไรสุทธิ = ยอดรายรับสุทธิรวม − ต้นทุน (ตาม DRX แสดง)
    if received_total > 0:
        net_profit   = received_total - total_cost
        gross_margin = net_profit / received_total * 100
    else:
        gross_margin = 0
        net_profit   = 0

    # ──────────────────────────────────────────────
    # 2. หมวดหมู่รายรับ
    # ──────────────────────────────────────────────
    categories = dr.get("categories", [])

    # ──────────────────────────────────────────────
    # 3. รายการเคสจาก DRX dashboard (op=5108)
    # ──────────────────────────────────────────────
    opd_list     = _extract_pet_list(raw.get("opd_active", []))   # filter_id=4
    admit_list   = _extract_pet_list(raw.get("admit", []))         # filter_id=5
    groom_list   = _extract_pet_list(raw.get("grooming", []))      # filter_id=9
    today_list   = _extract_pet_list(raw.get("today_cases", []))   # filter_id=1

    # นับ "เสร็จ" จาก status text ที่มีคำว่า ชำระ/รับยา
    done_count = sum(
        1 for c in today_list
        if "ชำระ" in c["status"] or "รับยา" in c["status"]
    )

    # ──────────────────────────────────────────────
    # 4. นัดหมายวันนี้ (จาก mapped appointments)
    # ──────────────────────────────────────────────
    today_iso     = datetime.now().strftime("%Y-%m-%d")
    today_appts   = [a for a in appts if a.get("date") == today_iso]

    # ──────────────────────────────────────────────
    # 5. สต็อก
    # ──────────────────────────────────────────────
    low_items     = [s for s in stock if s.get("level") == "low"]
    stock_tracked = any(s.get("qty", 0) > 0 for s in stock)

    # ──────────────────────────────────────────────
    # BUILD MESSAGE
    # ──────────────────────────────────────────────
    SEP = "━" * 17
    dr_date_str = dr.get("date", "") or thai_now()

    lines = [
        "📊 สรุปประจำวัน",
        "🏥 Dog and Cat Lovely",
        f"📅 {dr_date_str}",
        SEP,
        "",
        "💰 รายรับสุทธิรวม",
        f"    {fmt_baht(received_total)} บาท",
    ]
    # แสดง breakdown ถ้ามีมัดจำ
    if deposit > 0:
        lines.append(f"    (ค่าบริการ {fmt_baht(bill_total)} + รับมัดจำ {fmt_baht(deposit)})")
    if received_total > 0:
        lines.append(f"    กำไรสุทธิ {fmt_baht(net_profit)} ({gross_margin:.1f}%) • ต้นทุน {fmt_baht(total_cost)}")

    # หมวดหมู่
    if categories:
        lines += ["", "📦 รายละเอียดตามหมวดหมู่"]
        for cat in categories:
            val = float(cat.get("value", 0))
            if val > 0:
                label = cat.get("label", "")
                icon  = CAT_ICONS.get(label, "📌")
                lines.append(f"  {icon} {label}: {fmt_baht(val)}")

    # ──────────────────────────────────────────────
    # เคสวันนี้
    # ──────────────────────────────────────────────
    lines += ["", SEP, "🐾 สรุปเคสวันนี้"]
    lines.append(f"  📅 นัดหมายวันนี้:    {len(today_appts)} ราย")
    lines.append(f"  ✅ ตรวจเสร็จ-ชำระแล้ว: {done_count} ราย")
    if opd_list:
        lines.append(f"  🟢 OPD กำลังตรวจ:    {len(opd_list)} ราย")
        for c in opd_list[:3]:
            lines.append(f"      • {c['pet']} ({c['time']})")
    if groom_list:
        lines.append(f"  🛁 อาบน้ำ-ตัดขน:     {len(groom_list)} ราย")
        for c in groom_list[:3]:
            lines.append(f"      • {c['pet']} ({c['time']})")

    # ──────────────────────────────────────────────
    # Admit (ค้างคืน)
    # ──────────────────────────────────────────────
    lines += ["", SEP]
    if admit_list:
        lines.append(f"🛏️ สัตว์ป่วยใน Admit: {len(admit_list)} ตัว")
        for a in admit_list[:6]:
            lines.append(f"  • {a['pet']} (เจ้าของ: {a['owner']}) — {a['vet']}")
        if len(admit_list) > 6:
            lines.append(f"  ... และอีก {len(admit_list)-6} ตัว")
    else:
        lines.append("🛏️ สัตว์ป่วยใน Admit: ไม่มี")

    # ──────────────────────────────────────────────
    # ช่องทางชำระเงิน
    # ──────────────────────────────────────────────
    lines += ["", SEP, "💳 ช่องทางชำระเงิน"]
    lines.append(f"  💵 เงินสด:    {fmt_baht(cash)}")
    if transfer > 0:
        lines.append(f"  📱 โอน:       {fmt_baht(transfer)}")
    if (credit + debit) > 0:
        lines.append(f"  💳 บัตร:      {fmt_baht(credit + debit)}")
    if deposit > 0:
        lines.append(f"  🔒 รับมัดจำ:  {fmt_baht(deposit)}")
    lines.append(f"  📃 จำนวนบิล:  {bill_count} ใบ")
    if voided_count > 0:
        lines.append(f"  🗑️ ยกเลิก:    {voided_count} ใบ")

    # ──────────────────────────────────────────────
    # สต็อก — นับจำนวนชนิดยา/เวชภัณฑ์ + link DRX
    # ──────────────────────────────────────────────
    lines += ["", SEP, "📦 คลังสินค้า"]

    # นับตามประเภท (stockTypeId)
    TYPE_LABEL = {
        1: "💊 ยา",
        2: "🥫 อาหาร",
        3: "🩹 เวชภัณฑ์",
        4: "💉 วัคซีน",
        5: "📦 ของใช้",
        6: "🔧 อื่นๆ",
        7: "🧪 Lab",
    }
    raw_stock_all = raw.get("stock_all", []) or []
    if raw_stock_all:
        from collections import Counter as _Counter
        type_count = _Counter(
            s.get("stockTypeId", 0)
            for s in raw_stock_all
            if s.get("stockTypeId") in TYPE_LABEL
        )
        total_items = sum(type_count.values())
        lines.append(f"  รายการรวม: {total_items:,} ชนิด")
        for tid in sorted(type_count.keys()):
            label = TYPE_LABEL.get(tid, f"type={tid}")
            lines.append(f"    {label}: {type_count[tid]} ชนิด")
    else:
        lines.append("  (ดึงข้อมูลสต็อกไม่สำเร็จ)")

    # Link ไปหน้า DRX filter "ใกล้หมด"
    lines += [
        "",
        "⚠️ สต็อกใกล้หมด — ดูในระบบ DRX:",
        "🔗 http://dogcatlovely.thddns.net:8080/doctordogs/stock?type=-1",
        "   (เลือก filter \"สินค้า → ใกล้หมด\")",
    ]

    lines += ["", SEP, "🐾 Dog and Cat Lovely Pet Hospital"]
    return "\n".join(lines)


# ============================================================
#  LINE API
# ============================================================
def send_line_push(token: str, target_id: str, text: str, max_retries: int = 3) -> bool:
    """ส่ง push message ไปยัง LINE User/Group พร้อม retry เมื่อ network/DNS fail"""
    import time as _time
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "to":       target_id,
        "messages": [{"type": "text", "text": text}],
    }
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            if r.status_code == 200:
                print(f"✅ ส่ง LINE สำเร็จ! (attempt {attempt})")
                return True
            else:
                last_err = f"HTTP {r.status_code} — {r.text[:200]}"
                print(f"❌ attempt {attempt}: {last_err}")
                # 4xx errors ไม่ต้อง retry
                if 400 <= r.status_code < 500:
                    return False
        except requests.exceptions.ConnectionError as e:
            last_err = f"ConnectionError: {str(e)[:120]}"
            print(f"⚠️  attempt {attempt}/{max_retries}: {last_err}")
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            print(f"⚠️  attempt {attempt}/{max_retries}: {last_err}")
        if attempt < max_retries:
            wait = attempt * 10   # 10s, 20s, ...
            print(f"   รอ {wait}s แล้ว retry ...")
            _time.sleep(wait)
    print(f"❌ ส่ง LINE ล้มเหลว ({max_retries} ครั้ง) — {last_err}")
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
