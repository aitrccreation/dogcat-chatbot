"""
Appointment Sender: Excel → LINE Flex Message
==============================================
รัน 18:00 ทุกวัน:
  สำหรับแต่ละแถวใน Send_Queue:
    - คำนวณ days_until = appt_date - today
    - days_until == 3 → Round 1 (ส่งครั้งแรก)  → set sent_round_1_at
    - days_until == 1 → Round 2 (ย้ำ)            → set sent_round_2_at
    - days_until == 0 และ status != Confirmed → Round 3 (ย้ำสุดท้าย) → sent_round_3_at
    - skip ถ้า status = Confirmed/Reschedule
    - skip ถ้า NoLine (ไม่มี line_user_id) — แต่บันทึก log แจ้ง admin

ใช้ Flex Message + Postback action สำหรับปุ่ม ยืนยัน/เลื่อน
"""
import io
import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

import requests
from openpyxl import load_workbook

XLSX = Path(__file__).parent / "appointments.xlsx"

# LINE OA token สำหรับส่ง reminder — ใช้ LINE_OA_TOKEN ก่อน, fallback LINE_TOKEN
LINE_TOKEN = os.environ.get(
    "LINE_OA_TOKEN",
    os.environ.get("LINE_TOKEN", "")
).strip()

ADMIN_LINE_ID = os.environ.get("LINE_TARGET_ID", "Ude09abe7b1f73ee901c047ccfe693dd8").strip()

THAI_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai_date(iso: str) -> str:
    """'2026-05-21' → '21 พ.ค. 2569'"""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return f"{d.day} {THAI_MONTHS[d.month]} {d.year + 543}"
    except Exception:
        return iso


def thai_weekday(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
        return ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"][d.weekday()]
    except Exception:
        return ""


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Build Flex Message ──
def build_flex(qid: int, appt_date_iso: str, appt_time: str,
               pet_name: str, owner_name: str, vet: str, service: str,
               days_until: int, hn: str = "") -> dict:
    """LINE Flex Message bubble พร้อมปุ่มยืนยัน/เลื่อน"""
    appt_thai = thai_date(appt_date_iso)
    wd        = thai_weekday(appt_date_iso)
    time_str  = appt_time or "ตามเวลานัด"

    if days_until == 0:
        header_text = "🔔 นัดวันนี้!"
        header_color = "#DC2626"
    elif days_until == 1:
        header_text = "🔔 พรุ่งนี้มีนัดค่ะ"
        header_color = "#F59E0B"
    else:
        header_text = "📅 แจ้งเตือนนัดล่วงหน้า"
        header_color = "#2563EB"

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type":   "box",
            "layout": "vertical",
            "backgroundColor": header_color,
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": header_text, "weight": "bold",
                 "size": "lg", "color": "#FFFFFF"},
            ],
        },
        "body": {
            "type":   "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "box", "layout": "vertical", "spacing": "sm",
                    "contents": [
                        {"type": "text", "text": f"🐾 น้อง{pet_name or '-'}",
                         "weight": "bold", "size": "xl", "wrap": True},
                        {"type": "text", "text": f"เจ้าของ: {owner_name or '-'}",
                         "size": "sm", "color": "#6B7280"},
                    ],
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "box", "layout": "vertical", "spacing": "xs", "margin": "md",
                    "contents": [
                        {"type": "box", "layout": "baseline", "spacing": "sm",
                         "contents": [
                             {"type": "text", "text": "📅", "size": "sm", "flex": 0},
                             {"type": "text", "text": f"{appt_thai} ({wd})",
                              "size": "md", "weight": "bold", "wrap": True},
                         ]},
                        {"type": "box", "layout": "baseline", "spacing": "sm",
                         "contents": [
                             {"type": "text", "text": "⏰", "size": "sm", "flex": 0},
                             {"type": "text", "text": time_str, "size": "md", "wrap": True},
                         ]},
                        {"type": "box", "layout": "baseline", "spacing": "sm",
                         "contents": [
                             {"type": "text", "text": "🩺", "size": "sm", "flex": 0},
                             {"type": "text", "text": (service or "-")[:60],
                              "size": "sm", "wrap": True, "color": "#374151"},
                         ]},
                        {"type": "box", "layout": "baseline", "spacing": "sm",
                         "contents": [
                             {"type": "text", "text": "👨‍⚕️", "size": "sm", "flex": 0},
                             {"type": "text", "text": vet or "-", "size": "sm", "wrap": True},
                         ]},
                    ],
                },
            ],
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {"type": "button", "style": "primary", "color": "#10B981",
                 "action": {"type": "postback",
                            "label": "✅ ยืนยันนัด",
                            "data": f"appt=confirm&qid={qid}&hn={hn}&pet={pet_name or '-'}&date={appt_thai}",
                            "displayText": "ยืนยันนัด"}},
                {"type": "button", "style": "secondary",
                 "action": {"type": "postback",
                            "label": "🕐 ขอเลื่อนนัด",
                            "data": f"appt=reschedule&qid={qid}&hn={hn}&pet={pet_name or '-'}&date={appt_thai}",
                            "displayText": "ขอเลื่อนนัด"}},
            ],
        },
    }

    alt_text = f"นัด {appt_thai} น้อง{pet_name}"
    return {"type": "flex", "altText": alt_text[:400], "contents": bubble}


# ── LINE push ──
def send_flex(line_user_id: str, flex_msg: dict, max_retries: int = 3) -> tuple[bool, str]:
    if not LINE_TOKEN:
        return False, "no LINE token"
    import time as _t
    url = "https://api.line.me/v2/bot/message/push"
    payload = {"to": line_user_id, "messages": [flex_msg]}
    last_err = ""
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {LINE_TOKEN}",
                         "Content-Type": "application/json"},
                json=payload, timeout=15,
            )
            if r.status_code == 200:
                return True, ""
            last_err = f"HTTP {r.status_code} {r.text[:200]}"
            if 400 <= r.status_code < 500:
                return False, last_err  # don't retry 4xx
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < max_retries:
            _t.sleep(attempt * 5)
    return False, last_err


# ── notify admin (สำหรับ NoLine cases) ──
def notify_admin_nolist(no_line_rows: list[dict]):
    if not no_line_rows or not LINE_TOKEN or not ADMIN_LINE_ID:
        return
    lines = ["📞 ลูกค้าที่ต้องโทรนัดเอง (ไม่ได้ลงทะเบียน LINE)", "━━━━━━━━━━━━━━━━━"]
    for r in no_line_rows[:30]:
        lines.append(f"• {thai_date(r['appt_date'])} | น้อง{r['pet_name']} "
                     f"({r['owner_name']}) | HN {r['hn']}")
    if len(no_line_rows) > 30:
        lines.append(f"... และอีก {len(no_line_rows)-30} ราย")
    lines.append("")
    lines.append("🔗 ดูเต็มใน Excel: D:\\AI Dashboard\\appointments.xlsx")
    text = "\n".join(lines)

    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}",
                 "Content-Type": "application/json"},
        json={"to": ADMIN_LINE_ID,
              "messages": [{"type": "text", "text": text[:4900]}]},
        timeout=15,
    )


# ── MAIN ──
def main():
    args = set(sys.argv[1:])
    dry_run = "--dry-run" in args

    print("=" * 55)
    print("  📤 Appointment Sender — Excel → LINE")
    print(f"  Mode: {'DRY-RUN (ไม่ส่งจริง)' if dry_run else 'PRODUCTION'}")
    print("=" * 55)

    if not XLSX.exists():
        print(f"❌ {XLSX.name} missing"); sys.exit(1)

    wb = load_workbook(XLSX)
    ws = wb["Send_Queue"]
    today = date.today()

    stats = {"sent_r1": 0, "sent_r2": 0, "sent_r3": 0, "skip": 0, "noline": 0, "error": 0}
    noline_rows: list[dict] = []

    for row_idx in range(2, ws.max_row + 1):
        qid           = ws.cell(row=row_idx, column=1).value
        appt_date_iso = ws.cell(row=row_idx, column=2).value
        appt_time     = ws.cell(row=row_idx, column=3).value or ""
        hn            = ws.cell(row=row_idx, column=4).value or ""
        owner         = ws.cell(row=row_idx, column=5).value or ""
        pet           = ws.cell(row=row_idx, column=6).value or ""
        vet           = ws.cell(row=row_idx, column=7).value or ""
        service       = ws.cell(row=row_idx, column=8).value or ""
        line_uid      = ws.cell(row=row_idx, column=9).value or ""
        status        = (ws.cell(row=row_idx, column=10).value or "").strip()
        r1_at         = ws.cell(row=row_idx, column=11).value
        r2_at         = ws.cell(row=row_idx, column=12).value
        r3_at         = ws.cell(row=row_idx, column=13).value

        if not qid or not appt_date_iso:
            continue

        try:
            appt_d = datetime.strptime(str(appt_date_iso), "%Y-%m-%d").date()
        except Exception:
            continue

        days_until = (appt_d - today).days

        # Skip terminal states
        if status in ("Confirmed",):
            stats["skip"] += 1
            continue

        # Skip ที่ไม่ได้ลงทะเบียน — เก็บไว้แจ้ง admin
        if not line_uid or status == "NoLine":
            if days_until in (3, 1, 0):
                noline_rows.append({"hn": hn, "owner_name": owner, "pet_name": pet,
                                    "appt_date": str(appt_date_iso)})
                stats["noline"] += 1
            continue

        # Determine round
        which_round = None
        if days_until == 3 and not r1_at:
            which_round = 1
        elif days_until == 1 and not r2_at:
            which_round = 2
        elif days_until == 0 and not r3_at and status != "Reschedule":
            which_round = 3
        else:
            stats["skip"] += 1
            continue

        # Build + send
        flex = build_flex(qid, str(appt_date_iso), str(appt_time),
                          str(pet), str(owner), str(vet), str(service),
                          days_until, hn=str(hn))
        if dry_run:
            print(f"  [DRY] qid={qid} HN={hn} round={which_round} → {line_uid[:12]}...")
            ok, err = True, ""
        else:
            ok, err = send_flex(str(line_uid), flex)

        if ok:
            ts = now_iso()
            ws.cell(row=row_idx, column=10 + which_round, value=ts)  # sent_round_X_at
            if status == "Pending":
                ws.cell(row=row_idx, column=10, value="Sent")
            stats[f"sent_r{which_round}"] += 1
            print(f"  ✅ qid={qid} HN={hn} round={which_round} sent")
        else:
            stats["error"] += 1
            print(f"  ❌ qid={qid} HN={hn} round={which_round} FAIL: {err}")

    if not dry_run:
        wb.save(XLSX)

    # แจ้ง admin เรื่อง NoLine
    if noline_rows and not dry_run:
        notify_admin_nolist(noline_rows)

    print()
    print(f"📊 Summary: {stats}")
    if noline_rows:
        print(f"   {len(noline_rows)} รายที่ไม่มี LINE — แจ้ง admin แล้ว")


if __name__ == "__main__":
    main()
