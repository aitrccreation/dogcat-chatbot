"""
Appointment Sync: DRX → Excel
==============================
รัน 06:00 ทุกวัน:
  1. ดึงนัดหมายจาก DRX (op=36 grid + op=903 calendar)
  2. กรองเฉพาะนัดในช่วง T+0 ถึง T+5 (ครอบคลุม 3-วันก่อน + 1-วันก่อน + วันนัด)
  3. เขียนลง Sheet "DRX_Appointments"
  4. รวมกับ Sheet "Manual" → Sheet "Send_Queue" (skip รายการที่ status เป็น Confirmed อยู่แล้ว)
  5. ลง Sent_Log

Usage:
    python appointment_sync.py             # ใช้ drx_data.json (cached)
    python appointment_sync.py --fetch     # ดึง DRX ใหม่ก่อน
"""
import io
import json
import re
import sys
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from openpyxl import load_workbook

XLSX = Path(__file__).parent / "appointments.xlsx"
DRX_JSON = Path(__file__).parent / "drx_data.json"


# ── helpers ──
def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


THAI_MONTH = {"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
              "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}


def parse_thai_date(s: str) -> str | None:
    """'17 พ.ค. 2569' → '2026-05-17' (ISO)"""
    if not s:
        return None
    m = re.match(r"(\d+)\s+([ก-ฮ.]+)\s+(\d{4})", s.strip())
    if not m:
        return None
    day, month_th, year = m.groups()
    mo = THAI_MONTH.get(month_th)
    if not mo:
        return None
    return f"{int(year) - 543:04d}-{mo:02d}-{int(day):02d}"


def fetch_drx_if_needed(force: bool = False):
    """รัน drx_bridge.py ถ้า drx_data.json เก่ากว่า 24 ชม. หรือ force"""
    if force or not DRX_JSON.exists():
        print("🔄 Fetching fresh DRX data...")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "drx_bridge.py")],
            capture_output=True, text=True, encoding="utf-8"
        )
        if result.returncode != 0:
            print(f"[WARN] drx_bridge failed: {result.stderr[:200]}")


def load_drx_appointments() -> list[dict]:
    """อ่าน _raw.appointments + _raw.calendar จาก drx_data.json
    Return: list of {appt_id, hn, owner_name, pet_name, vet, service, appt_date, appt_time}
    """
    if not DRX_JSON.exists():
        print("❌ drx_data.json missing — run drx_bridge.py first")
        return []
    data = json.loads(DRX_JSON.read_text(encoding="utf-8"))

    raw = data.get("_raw", {}) or {}
    appts = []

    # Source 1: _raw.appointments (grid op=36) — มีรายละเอียดครบ
    for r in raw.get("appointments", []):
        if not isinstance(r, dict):
            continue
        cells = r.get("cell", [])
        if len(cells) < 13:
            continue
        # cell layout:
        # [0] date "17 พ.ค. 2569"
        # [5] "pet/owner" — แต่ใน DRX จริง คือ "owner ชื่อ" + pet ใน cell อื่น
        # [6] phone "08xxxxxxxx"
        # [8] service category "ตรวจทั่วไป"
        # [9] service detail
        # [11] HN "690200-1"
        # [12] vet name
        appt_date = parse_thai_date(str(cells[0]))
        if not appt_date:
            continue
        pet_owner = str(cells[5]) if len(cells) > 5 else ""
        if "/" in pet_owner:
            pet, owner = pet_owner.split("/", 1)
        else:
            pet, owner = pet_owner, ""
        appts.append({
            "appt_id":    f"DRX-{r.get('id', '')}",
            "hn":         str(cells[11]) if len(cells) > 11 else "",
            "owner_name": owner.strip(),
            "pet_name":   pet.strip(),
            "vet":        str(cells[12]) if len(cells) > 12 else "",
            "service":    f"{cells[8]} — {cells[9]}" if len(cells) > 9 else str(cells[8] if len(cells) > 8 else ""),
            "appt_date":  appt_date,
            "appt_time":  "",   # grid view ไม่มีเวลาแน่นอน
            "source":     "drx",
        })

    return appts


def write_drx_sheet(appts: list[dict]):
    """เขียน DRX_Appointments sheet — overwrite ทั้ง sheet"""
    wb = load_workbook(XLSX)
    ws = wb["DRX_Appointments"]
    # clear rows (เก็บ header)
    ws.delete_rows(2, ws.max_row)
    for a in appts:
        ws.append([
            a["appt_id"], a["hn"], a["owner_name"], a["pet_name"],
            a["vet"], a["service"], a["appt_date"], a["appt_time"],
            a["source"], now_iso(),
        ])
    wb.save(XLSX)
    print(f"✅ DRX_Appointments: {len(appts)} rows")


def collect_send_queue():
    """รวม DRX + Manual → Send_Queue ที่ตรงเงื่อนไข reminder window
    เงื่อนไข: appt_date ∈ [today, today + 5 days]
    Skip: รายการที่อยู่ใน Send_Queue แล้วและ status=Confirmed
    """
    wb = load_workbook(XLSX)
    today = datetime.now().date()
    window_end = today + timedelta(days=5)

    def in_window(d: str) -> bool:
        try:
            dt = datetime.strptime(d, "%Y-%m-%d").date()
            return today <= dt <= window_end
        except Exception:
            return False

    # Customer map: HN → line_user_id
    ws_c = wb["Customers"]
    hn_to_userid: dict[str, str] = {}
    for row in ws_c.iter_rows(min_row=2, values_only=True):
        if row[1] and row[0]:
            hn_to_userid[str(row[1]).strip()] = str(row[0]).strip()

    # อ่าน existing queue → จำ confirmed
    ws_q = wb["Send_Queue"]
    existing_confirmed: set[tuple[str, str]] = set()  # (hn, appt_date)
    existing_keys: dict[tuple[str, str], int] = {}    # (hn, appt_date) → row_idx
    for row_idx, row in enumerate(ws_q.iter_rows(min_row=2, values_only=True), start=2):
        if not row[0]: continue
        key = (str(row[3] or ""), str(row[1] or ""))
        existing_keys[key] = row_idx
        if str(row[9] or "").lower() == "confirmed":
            existing_confirmed.add(key)

    # collect candidates
    def collect_from_sheet(sheet_name: str, source: str):
        ws_s = wb[sheet_name]
        out = []
        for row in ws_s.iter_rows(min_row=2, values_only=True):
            if not row[0]:  # appt_id
                continue
            appt_date = str(row[6] or "")
            if not in_window(appt_date):
                continue
            hn = str(row[1] or "").strip()
            key = (hn, appt_date)
            if key in existing_confirmed:
                continue
            out.append({
                "appt_id":    str(row[0]),
                "hn":         hn,
                "owner_name": str(row[2] or ""),
                "pet_name":   str(row[3] or ""),
                "vet":        str(row[4] or ""),
                "service":    str(row[5] or ""),
                "appt_date":  appt_date,
                "appt_time":  str(row[7] or ""),
                "source":     source,
            })
        return out

    drx_appts    = collect_from_sheet("DRX_Appointments", "drx")
    manual_appts = collect_from_sheet("Manual", "manual")
    candidates = drx_appts + manual_appts

    # update Send_Queue
    next_qid = max([row[0] for row in ws_q.iter_rows(min_row=2, values_only=True) if row[0]] or [0]) + 1
    added = 0
    for a in candidates:
        key = (a["hn"], a["appt_date"])
        if key in existing_keys:
            # update existing row (เผื่อข้อมูล DRX update)
            row_idx = existing_keys[key]
            ws_q.cell(row=row_idx, column=2, value=a["appt_date"])
            ws_q.cell(row=row_idx, column=3, value=a["appt_time"])
            ws_q.cell(row=row_idx, column=5, value=a["owner_name"])
            ws_q.cell(row=row_idx, column=6, value=a["pet_name"])
            ws_q.cell(row=row_idx, column=7, value=a["vet"])
            ws_q.cell(row=row_idx, column=8, value=a["service"])
            # update line_user_id ถ้าลูกค้าเพิ่งลงทะเบียน
            user_id = hn_to_userid.get(a["hn"], "")
            if user_id and not ws_q.cell(row=row_idx, column=9).value:
                ws_q.cell(row=row_idx, column=9, value=user_id)
            continue
        # new row
        user_id = hn_to_userid.get(a["hn"], "")
        status = "Pending" if user_id else "NoLine"
        ws_q.append([
            next_qid, a["appt_date"], a["appt_time"], a["hn"],
            a["owner_name"], a["pet_name"], a["vet"], a["service"],
            user_id, status,
            "", "", "",     # sent_round_1/2/3_at
            "", "",          # response_at, response
            a["source"],
        ])
        next_qid += 1
        added += 1

    wb.save(XLSX)
    return added, len(candidates)


def sync_customers_from_railway():
    """ดึง customer registrations จาก Railway bot API → อัพ local Customers sheet
    ใช้ env var: RAILWAY_BOT_URL, INTERNAL_API_KEY
    """
    import os
    try:
        import requests as _req
    except ImportError:
        print("   [customer-sync] requests not installed — skip")
        return 0

    bot_url = os.environ.get("RAILWAY_BOT_URL", "").rstrip("/")
    api_key = os.environ.get("INTERNAL_API_KEY", "dogcatlovely_internal_2026")

    if not bot_url:
        print("   [customer-sync] RAILWAY_BOT_URL not set — skip")
        return 0

    try:
        r = _req.get(
            f"{bot_url}/api/customers",
            headers={"X-API-Key": api_key},
            timeout=15,
        )
        if r.status_code == 403:
            print("   [customer-sync] ❌ Unauthorized — check INTERNAL_API_KEY")
            return 0
        r.raise_for_status()
        data = r.json()
        customers = data.get("customers", [])
        if not customers:
            print("   [customer-sync] ไม่มีลูกค้าลงทะเบียนบน Railway")
            return 0

        import appointment_db as adb
        updated = 0
        for c in customers:
            uid = c.get("line_user_id")
            hn  = c.get("hn")
            if uid and hn:
                adb.register_customer(
                    line_user_id=uid,
                    hn=hn,
                    owner_name=c.get("owner_name") or "",
                    pet_name=c.get("pet_name") or "",
                    phone=c.get("phone") or "",
                )
                updated += 1
        print(f"   [customer-sync] ✅ synced {updated} customers จาก Railway")
        return updated
    except Exception as e:
        print(f"   [customer-sync] ⚠️ error: {e}")
        return 0


def main():
    args = set(sys.argv[1:])
    fetch_fresh = "--fetch" in args

    print("=" * 55)
    print("  📅 Appointment Sync — DRX → Excel")
    print("=" * 55)

    if fetch_fresh:
        fetch_drx_if_needed(force=True)

    if not XLSX.exists():
        print(f"❌ {XLSX.name} missing — run init_appointments_xlsx.py first")
        sys.exit(1)

    # 0. sync customer registrations จาก Railway bot
    sync_customers_from_railway()

    # 1. ดึง DRX → DRX_Appointments
    drx = load_drx_appointments()
    print(f"   loaded {len(drx)} appointments จาก DRX")
    write_drx_sheet(drx)

    # 2. รวมเข้า Send_Queue (ดึง line_user_id จาก Customers sheet อัตโนมัติ)
    added, total = collect_send_queue()
    print(f"   Send_Queue: +{added} new (จาก {total} candidates ในช่วง T..T+5)")

    # 3. log
    try:
        import appointment_db as adb
        adb.log_event("Sync", "", "", f"DRX:{len(drx)} → Queue+{added}", "OK")
    except Exception:
        pass

    print()
    print("[DONE] Sync completed")


if __name__ == "__main__":
    main()
