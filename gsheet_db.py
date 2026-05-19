"""
Google Sheets-based Customer DB
================================
ใช้ Google Sheet เป็น persistent storage สำหรับ Customers
แทน xlsx (ที่หายตอน Railway redeploy)

ENV VARS:
  GOOGLE_CREDS_JSON  — Service account credentials JSON (string)
  GOOGLE_CREDS_PATH  — หรือ path ไปยังไฟล์ JSON
  GOOGLE_SHEET_ID    — Sheet ID (จาก URL ของ Google Sheet)

ถ้าไม่ได้ตั้ง env vars → ฟังก์ชันจะคืน None / [] → caller fallback ไป xlsx
"""
import os
import json
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

HEADERS = [
    "line_user_id", "hn", "owner_name", "pet_name", "pet_type",
    "phone", "registered_at", "last_active", "note",
]

_sheet = None
_init_attempted = False
_disabled = False


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_sheet():
    """Lazy init — connect to Google Sheet เฉพาะเมื่อใช้ครั้งแรก"""
    global _sheet, _init_attempted, _disabled
    if _disabled:
        return None
    if _sheet is not None:
        return _sheet
    if _init_attempted:
        return _sheet  # already tried, failed

    _init_attempted = True

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log.warning("[gsheet] gspread not installed — pip install gspread google-auth")
        _disabled = True
        return None

    sheet_id   = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "").strip()
    creds_path = os.environ.get("GOOGLE_CREDS_PATH", "").strip()

    if not sheet_id:
        log.info("[gsheet] GOOGLE_SHEET_ID not set — using xlsx only")
        _disabled = True
        return None

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    try:
        if creds_json:
            creds_dict = json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        elif creds_path and os.path.exists(creds_path):
            creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        else:
            log.warning("[gsheet] no credentials — set GOOGLE_CREDS_JSON or GOOGLE_CREDS_PATH")
            _disabled = True
            return None

        client = gspread.authorize(creds)
        wb = client.open_by_key(sheet_id)

        # ใช้ worksheet "Customers" — ถ้าไม่มีให้สร้าง
        try:
            _sheet = wb.worksheet("Customers")
        except Exception:
            _sheet = wb.add_worksheet(title="Customers", rows=1000, cols=len(HEADERS))
            _sheet.append_row(HEADERS)

        log.info("[gsheet] connected to Google Sheet successfully")
        return _sheet
    except Exception as e:
        log.exception(f"[gsheet] init failed: {e}")
        _disabled = True
        return None


def is_enabled() -> bool:
    """True ถ้า Google Sheet พร้อมใช้งาน"""
    return _get_sheet() is not None


def find_customer_by_user_id(line_user_id: str) -> Optional[dict]:
    sheet = _get_sheet()
    if not sheet:
        return None
    try:
        records = sheet.get_all_records()
        for r in records:
            if str(r.get("line_user_id", "")) == line_user_id:
                return r
    except Exception as e:
        log.warning(f"[gsheet] find_by_uid error: {e}")
    return None


def find_customers_by_hn(hn: str) -> list[dict]:
    sheet = _get_sheet()
    if not sheet:
        return []
    try:
        records = sheet.get_all_records()
        return [r for r in records if str(r.get("hn", "")).strip() == hn.strip()]
    except Exception as e:
        log.warning(f"[gsheet] find_by_hn error: {e}")
        return []


def get_all_customers() -> list[dict]:
    sheet = _get_sheet()
    if not sheet:
        return []
    try:
        return sheet.get_all_records()
    except Exception as e:
        log.warning(f"[gsheet] get_all error: {e}")
        return []


def register_customer(
    line_user_id: str,
    hn: str,
    owner_name: str = "",
    pet_name:   str = "",
    pet_type:   str = "",
    phone:      str = "",
    note:       str = "",
    max_per_hn: int = 2,
) -> Optional[dict]:
    """เพิ่ม/อัพเดต customer ใน Google Sheet
    Return: dict ถ้าสำเร็จ, None ถ้า HN ครบ quota
    """
    sheet = _get_sheet()
    if not sheet:
        return None
    try:
        records = sheet.get_all_records()
        now = _now_iso()

        # หา existing row ของ line_user_id นี้
        for idx, r in enumerate(records, start=2):   # start=2 because row 1 = headers
            if str(r.get("line_user_id", "")) == line_user_id:
                # update
                sheet.update(f"A{idx}:I{idx}", [[
                    line_user_id,
                    hn,
                    owner_name or r.get("owner_name", ""),
                    pet_name   or r.get("pet_name", ""),
                    pet_type   or r.get("pet_type", ""),
                    phone      or r.get("phone", ""),
                    r.get("registered_at", now),
                    now,
                    note or r.get("note", ""),
                ]])
                return {
                    "line_user_id": line_user_id, "hn": hn,
                    "owner_name": owner_name, "pet_name": pet_name,
                    "registered_at": r.get("registered_at", now),
                    "last_active": now,
                }

        # คนใหม่ — ตรวจ quota
        existing_count = sum(1 for r in records if str(r.get("hn", "")).strip() == hn.strip())
        if existing_count >= max_per_hn:
            log.warning(f"[gsheet] HN {hn} quota full ({existing_count}/{max_per_hn})")
            return None

        # append new
        sheet.append_row([
            line_user_id, hn, owner_name, pet_name, pet_type, phone,
            now, now, note,
        ])
        log.info(f"[gsheet] registered {line_user_id[:16]}... HN={hn}")
        return {
            "line_user_id": line_user_id, "hn": hn,
            "owner_name": owner_name, "pet_name": pet_name,
            "registered_at": now, "last_active": now,
        }
    except Exception as e:
        log.exception(f"[gsheet] register error: {e}")
        return None


def count_customers_by_hn(hn: str) -> int:
    return len(find_customers_by_hn(hn))


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    if not is_enabled():
        print("[gsheet] NOT enabled — set GOOGLE_SHEET_ID and credentials")
        sys.exit(1)

    print("=== All customers ===")
    for c in get_all_customers():
        print(f"  uid={c.get('line_user_id','')[:20]}... hn={c.get('hn')} name={c.get('owner_name')}")
