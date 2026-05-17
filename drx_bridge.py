"""
DRX Bridge v2 - ดึงข้อมูลจากระบบ Dog and Cat Lovely DRX
=============================================================
จากการวิเคราะห์ HAR พบว่า:
  - API ทั้งหมดใช้ POST http://...../doctordogs/doctordogs
  - Parameters: dda=<module>&op=<operation>&...
  - Format: application/x-www-form-urlencoded
  - Auth: Java session cookie (JSESSIONID)

วิธีใช้:
  pip install requests beautifulsoup4
  python drx_bridge.py
"""
import json
import sys
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

# โหลด .env (local dev)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("กรุณาติดตั้ง dependencies ก่อน:")
    print("  pip install requests beautifulsoup4 pycryptodome")
    sys.exit(1)

try:
    from Crypto.Hash import keccak as _keccak
    def sha3_drx(password: str) -> str:
        """CryptoJS.SHA3(password) = Keccak-512 (ไม่ใช่ NIST SHA3)"""
        k = _keccak.new(digest_bits=512)
        k.update(password.encode("utf-8"))
        return k.hexdigest()
except ImportError:
    print("กรุณาติดตั้ง pycryptodome ก่อน:")
    print("  pip install pycryptodome")
    sys.exit(1)

# ===== CONFIG =====
import os
BASE      = os.environ.get("DRX_BASE", "http://dogcatlovely.thddns.net:8080")
API       = f"{BASE}/doctordogs/doctordogs"
LOGIN_URL = f"{BASE}/doctordogs/login"
DASH_URL  = f"{BASE}/doctordogs/dashboard"
USERNAME  = os.environ.get("DRX_USERNAME", "dogandcatlovely")
PASSWORD  = os.environ.get("DRX_PASSWORD", "")
OUTPUT    = Path(__file__).parent / "drx_data.json"

# ===== HELPERS =====

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "th-TH,th;q=0.9",
    })
    return s


def api_post(session: requests.Session, params: dict) -> dict | list | None:
    """POST ไปที่ /doctordogs/doctordogs พร้อม headers ถูกต้อง"""
    headers = {
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          DASH_URL,
        "Origin":           BASE,
    }
    try:
        r = session.post(API, data=params, headers=headers, timeout=20)
        if r.status_code != 200:
            print(f"    HTTP {r.status_code}")
            return None
        text = r.text.strip()
        if not text:
            return None
        # เช็กว่า redirect ไป login หรือเปล่า
        if '<html' in text[:200].lower() and 'login' in text[:500].lower():
            print("    [WARN] redirected to login page")
            return None
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    JSON decode error: {e} | text[:100]={r.text[:100]!r}")
        return None
    except requests.RequestException as e:
        print(f"    Request error: {e}")
        return None


# ===== LOGIN =====

def login(session: requests.Session) -> bool:
    """
    DRX Login Flow:
      1. GET /login  → รับ JSESSIONID cookie
      2. POST /doctordogs/doctordogs  dda=user&op=1101&username=X&password=Keccak512(pass)
      3. Response: true = success
    หมายเหตุ: password ต้อง hash ด้วย Keccak-512 (CryptoJS.SHA3 v3.1.2)
    """
    print(f"  กำลัง login ที่ {LOGIN_URL} ...")

    # --- Step 1: GET login page เพื่อรับ JSESSIONID ---
    try:
        r = session.get(LOGIN_URL, timeout=15)
    except requests.RequestException as e:
        print(f"  [ERROR] เชื่อมต่อไม่ได้: {e}")
        return False

    if r.status_code != 200:
        print(f"  [ERROR] GET /login ได้ HTTP {r.status_code}")
        return False

    jsid = session.cookies.get("JSESSIONID", "none")
    print(f"  JSESSIONID: {jsid[:16]}...")

    # --- Step 2: POST login ด้วย Keccak-512 hash ---
    crypto_password = sha3_drx(PASSWORD)
    headers = {
        "Accept":           "application/json, text/javascript, */*; q=0.01",
        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          LOGIN_URL,
        "Origin":           BASE,
    }
    try:
        r = session.post(
            API,
            data={"dda": "user", "op": "1101",
                  "username": USERNAME, "password": crypto_password},
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  [ERROR] POST login: {e}")
        return False

    text = r.text.strip()
    if r.status_code == 200 and text == "true":
        print(f"  [OK] Login สำเร็จ!")
        # Set client-side cookies ที่ DRX JS ตั้งไว้
        session.cookies.set("room_id", "-1", domain=BASE.split("//")[1].split(":")[0])
        session.cookies.set("cashier", "N/A", domain=BASE.split("//")[1].split(":")[0])
        session.cookies.set("cashier-period", "N/A", domain=BASE.split("//")[1].split(":")[0])
        session.cookies.set("cashier-activity-id", "-1", domain=BASE.split("//")[1].split(":")[0])
        return True

    print(f"  [FAIL] Login ได้ HTTP {r.status_code}, response: {text[:100]}")
    return False


# ===== FETCH DATA =====

def fetch_summary(session) -> dict:
    """dda=data&op=302 → จำนวน customers / pets"""
    data = api_post(session, {"dda": "data", "op": "302"})
    if not data:
        return {}
    return data.get("profilesSummaryData", data)


def fetch_cases(session, rows=100) -> list:
    """dda=pet&op=61&checked_pet_filter_id=7 → รายการเคสรักษาทั้งหมด
    filter_id: 1=วันนี้, 2=ล่าสุด, 7=ทั้งหมด
    cell: [0]=datetime [1]='pet/owner' [2]=HN [3]=status [4]=vet
    """
    today = int(time.time())
    result = api_post(session, {
        "dda": "pet",
        "op": "61",
        "checked_pet_filter_id": "7",   # 7 = ทั้งหมด
        "checked_pet_add_date_value": "",
        "_search": "false",
        "nd": str(today * 1000),
        "rows": str(rows),
        "page": "1",
        "sidx": "checked_pet_add_datetime",
        "sord": "desc",
    })
    if not result:
        return []
    return result.get("rows", [])


def _fetch_checked_pet(session, filter_id: int, rows: int = 50) -> list:
    """ฐาน: dda=pet&op=5108 — รายการเคสตามตัวกรอง
    filter_id reference (จาก dashboard DRX):
       1 = วันนี้ทั้งหมด (รวมทุกประเภท)
       2 = ล่าสุด
       4 = สัตว์ป่วยนอก OPD (กำลังตรวจ)
       5 = สัตว์ป่วยใน Admit (ค้างคืน)
       7 = ทั้งหมด
       9 = อาบน้ำ-ตัดขน
    """
    today = int(time.time())
    result = api_post(session, {
        "dda": "pet",
        "op":  "5108",
        "checked_pet_filter_id":    str(filter_id),
        "checked_pet_add_date_value": "",
        "_search": "false",
        "nd":   str(today * 1000),
        "rows": str(rows),
        "page": "1",
        "sidx": "checked_pet_modify_datetime",
        "sord": "desc",
    })
    if not result:
        return []
    return result.get("rows", []) if isinstance(result, dict) else []


def fetch_opd_active(session) -> list:
    """สัตว์ป่วยนอก OPD — กำลังตรวจอยู่"""
    return _fetch_checked_pet(session, filter_id=4)


def fetch_admit_active(session) -> list:
    """สัตว์ป่วยใน Admit — ค้างคืน รพ."""
    return _fetch_checked_pet(session, filter_id=5)


def fetch_grooming_active(session) -> list:
    """อาบน้ำ-ตัดขน — กำลังให้บริการ"""
    return _fetch_checked_pet(session, filter_id=9)


def fetch_today_cases(session) -> list:
    """เคสวันนี้ทั้งหมด (ทุกประเภท)"""
    return _fetch_checked_pet(session, filter_id=1)


def fetch_appointments(session, rows=100) -> list:
    """dda=pet&op=36 → นัดหมาย"""
    today = int(time.time())
    result = api_post(session, {
        "dda": "pet",
        "op": "36",
        "appointment_filter_id": "1",
        "appointment_doctor_id": "-1",
        "is_grid_table": "true",
        "_search": "false",
        "nd": str(today * 1000),
        "rows": str(rows),
        "page": "1",
        "sidx": "appointment_from_datetime",
        "sord": "asc",
    })
    if not result:
        return []
    return result.get("rows", [])


def fetch_calendar(session, days_back=7, days_fwd=30) -> list:
    """dda=pet&op=903 → ปฏิทินนัดหมาย"""
    now = datetime.now()
    start = int((now - timedelta(days=days_back)).timestamp())
    end   = int((now + timedelta(days=days_fwd)).timestamp())
    result = api_post(session, {
        "dda": "pet",
        "op": "903",
        "start": str(start),
        "end":   str(end),
        "filter_exclude_appointment_mode_id_list": "",
        "filter_exclude_special_clinic_id_list":  "",
        "filter_exclude_other_service_id_list":   "",
        "specific_doctor_id_list": "-1",
    })
    if not result:
        return []
    return result if isinstance(result, list) else []


def fetch_stock(session, max_pages=4) -> list:
    """dda=stock&op=520 → รายการสต็อกทั้งหมด (paginated)
    Response: {"stocks":[...], "page":1, "numberOfPages":N, "numberOfStocks":N}
    Stock keys: stockUID, stockId, stockName, stockTypeId, stockNumber,
                totalStockRemaining, stockNumberAlert, stockSalePrice,
                stockStatus, stockExpireStatus, stockNumberUnit
    rows=200 per page, max 4 pages = up to 800 items
    """
    all_stocks = []
    for page in range(1, max_pages + 1):
        result = api_post(session, {
            "dda": "stock",
            "op": "520",
            "_search": "false",
            "rows": "200",
            "page": str(page),
            "sidx": "",
            "sord": "asc",
            "type": "-1",
            "wordsearch": "",
        })
        if not result:
            break
        if isinstance(result, dict):
            stocks = result.get("stocks", [])
            all_stocks.extend(stocks)
            total_pages = result.get("numberOfPages", 1)
            if page >= total_pages:
                break
        elif isinstance(result, list):
            all_stocks.extend(result)
            break
    return all_stocks


def fetch_stock_expiry(session) -> list:
    """ดึง stock ที่ใกล้หมดอายุ / ระวัง (จาก op=1515 = species list, skip)"""
    # op=1515 is actually species list, not expiry
    # Use stockExpireStatus from fetch_stock instead
    return []


def fetch_daily_revenue(session, date_be: str = None) -> dict:
    """dda=bill&op=1032&report_type=daily → รายงานการขายรายวัน (เงินรับ/กำไร/breakdown)
    Response สำคัญ:
      - sumDailyAllReceivedMoney: ยอดเงินรับทั้งหมด (รวมมัดจำ) ← "รวมรายรับทั้งหมด"
      - totalBillPriceAmount:     ยอดบิลรวม (ไม่รวมมัดจำ)
      - sumCash, sumMoneyTransfer, sumCredit, sumDebit, sumCheque: breakdown ช่องชำระ
      - sumDeposit, sumDepositMoneyTransfer: มัดจำ
      - dailyNetRevenue:          กำไรสุทธิ
      - totalCost:                ต้นทุนรวม
      - numberOfBill:             จำนวนบิล
      - pieChartDataItems:        รายได้แยกตามหมวด (รายการยา/ค่าตรวจ/Lab/...)
    date_be: 'YYYY-MM-DD' พ.ศ. (เช่น '2569-05-14' = 14 พ.ค. 2026)
    """
    if date_be is None:
        d = datetime.now()
        date_be = f"{d.year + 543}-{d.month:02d}-{d.day:02d}"
    result = api_post(session, {
        "dda":                   "bill",
        "op":                    "1032",
        "report_type":           "daily",
        "summary_date":          date_be,
        "is_time_specific_flag": "false",
        "from_time":             "",
        "to_time":               "",
        "cashier_point_name":    "-1",
        "cashier_period_name":   "-1",
    })
    return result or {}


def fetch_finance_monthly(session, year=None) -> dict:
    """dda=data&op=303 → รายรับ-รายจ่ายรายเดือน (monetary=true = บาท)"""
    if year is None:
        year = datetime.now().year
    result = api_post(session, {
        "dda":         "data",
        "op":          "303",
        "year":        str(year),
        "monetary":    "true",   # true = บาท, false = จำนวนเคส
        "filter_mode": "-1",
    })
    return result or {}


def fetch_finance_summary(session) -> dict:
    """dda=data&op=300 → สรุปการเงิน"""
    result = api_post(session, {"dda": "data", "op": "300"})
    return result or {}


def fetch_vets(session) -> list:
    """dda=setting&op=806 → รายชื่อสัตวแพทย์"""
    result = api_post(session, {"dda": "setting", "op": "806", "user_type": "1"})
    if not result:
        return []
    return result if isinstance(result, list) else []


def fetch_customers_summary(session) -> dict:
    """dda=customer&op=118 → สรุปข้อมูลลูกค้า"""
    result = api_post(session, {"dda": "customer", "op": "118"})
    return result or {}


# ===== DATA MAPPING =====
# map raw DRX rows → frontend format

def _get(row: dict | list, *keys):
    """ดึง value จาก row ที่อาจเป็น dict หรือ list (jqGrid cell[])"""
    if isinstance(row, dict):
        for k in keys:
            if k in row:
                v = row[k]
                return v if v is not None else ""
        # ลอง cell array ถ้ามี
        cells = row.get("cell", [])
        if cells and isinstance(cells, list):
            return cells[0] if cells else ""
        return ""
    if isinstance(row, list):
        return row[0] if row else ""
    return ""


def map_status(raw) -> str:
    s = str(raw).lower()
    if any(w in s for w in ("active", "opd", "กำลัง", "ตรวจ", "1")):
        return "active"
    if any(w in s for w in ("followup", "follow", "นัด", "ติดตาม")):
        return "followup"
    return "done"


PET_TYPE_MAP = {
    "สุนัข": "dog", "dog": "dog", "หมา": "dog",
    "แมว": "cat", "cat": "cat",
}


def map_pet_type(raw) -> str:
    r = str(raw).lower()
    for k, v in PET_TYPE_MAP.items():
        if k in r:
            return v
    return "other"


STATUS_COLORS = {
    "dog":   "#3b82f6",
    "cat":   "#f59e0b",
    "other": "#10b981",
}


def map_cases(rows: list) -> list:
    """
    DRX checked pet row (filter_id=7) cell layout:
      cell[0]: datetime  "13 พ.ค. 69 เวลา 19:53"
      cell[1]: "pet_name/owner_name"
      cell[2]: HN  "690201-1"
      cell[3]: status text
      cell[4]: vet name
      cell[5..]: IDs etc.
    """
    THAI_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                   "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    out = []

    def parse_drx_date(s: str) -> str:
        """'13 พ.ค. 69 เวลา 19:53' → display string"""
        if not s:
            return ""
        # already good format - return as-is but convert BE→CE year
        # ปี พ.ศ. 69 = 2569 = ค.ศ. 2026
        return s.replace(" เวลา ", " ").strip()

    for i, row in enumerate(rows):
        if not row:
            continue
        d = row if isinstance(row, dict) else {}
        cells = d.get("cell", [])
        row_id = d.get("id", i + 1)

        def c(idx):
            return str(cells[idx]) if cells and idx < len(cells) and cells[idx] is not None else ""

        datetime_raw = c(0)   # "13 พ.ค. 69 เวลา 19:53"
        pet_owner    = c(1)   # "ข้าวโพดปิ้ง/สุกัญญา อุรุวรรณ"
        hn           = c(2)   # "690204-1"
        status_text  = c(3)   # "รับยาและชำระค่าใช้จ่ายแล้ว"
        vet_name     = c(4)   # "นารีรัตน์ ผลทิพย์"
        invoice_id   = c(8)   # "EXP1000611"

        # Split pet/owner
        if "/" in pet_owner:
            parts = pet_owner.split("/", 1)
            pet_name = parts[0].strip()
            owner    = parts[1].strip()
        else:
            pet_name = pet_owner
            owner    = "ไม่ระบุ"

        # Status mapping based on Thai text
        status_lo = status_text.lower()
        if "ชำระ" in status_text or "รับยา" in status_text:
            status = "done"
        elif "นัด" in status_text or "ติดตาม" in status_text:
            status = "followup"
        else:
            status = "active"

        # Clean HN
        hn_disp = f"HN-{hn}" if hn and not hn.startswith("HN") else hn

        # case_id: use invoice_id or row_id
        case_id = invoice_id if invoice_id else f"CS-{datetime.now().year}-{row_id:04d}"

        out.append({
            "id":     case_id,
            "hn":     hn_disp,
            "pet":    {
                "name":  pet_name or "ไม่ระบุ",
                "type":  "other",       # DRX ไม่ส่ง type มาใน list view
                "breed": "",
                "color": "#6366f1",
            },
            "owner":     owner,
            "symptom":   "",            # ต้องดึงจาก detail endpoint
            "diagnosis": "",
            "vet":       vet_name or "ไม่ระบุ",
            "date":      datetime_raw,
            "status":    status,
        })
    return out


def map_appointments(rows: list, cal_events: list = None) -> list:
    """
    Calendar event format (op=903):
      {"title":"pet,service,doctor","start":"2026-05-14 00:00","end":"...","allDay":true,"color":"lightgreen"}

    Grid row (op=36) cell layout:
      cell[0]: date "14 พ.ค. 2569"
      cell[5]: "owner/pet"
      cell[8]: service
      cell[12]: vet
      cell[15]: color
      cell[17]: datetime "07 พ.ค. 69 เวลา 10:25"
    """
    COLOR_TYPE = {
        "lightgreen":  "dog",
        "#F5DE1A":     "cat",
        "lightyellow": "cat",
        "yellow":      "cat",
        "lightblue":   "dog",
        "#3b82f6":     "dog",
        "#f59e0b":     "other",
        "orange":      "other",
        "purple":      "other",
        "pink":        "other",
    }
    out = []
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Use calendar events as primary source (filter upcoming only)
    source = cal_events if cal_events else []

    for item in source:
        if not isinstance(item, dict) or "title" not in item:
            continue

        # Filter to today and future
        start_dt = item.get("start", "")
        if start_dt and start_dt[:10] < today_str:
            continue   # skip past appointments

        parts    = item.get("title", "").split(",")
        pet_name = parts[0].strip() if parts else ""
        service  = parts[1].strip() if len(parts) > 1 else "ตรวจทั่วไป"
        vet      = parts[2].strip() if len(parts) > 2 else "ไม่ระบุ"

        color    = item.get("color", "")
        pet_type = COLOR_TYPE.get(color, "other")

        # Time
        is_all_day = item.get("allDay", True)
        if is_all_day or len(start_dt) <= 10:
            time_str = "ทั้งวัน"
        else:
            time_str = start_dt[11:16]

        # Status based on date
        if start_dt[:10] == today_str:
            status = "waiting"
        else:
            status = "waiting"   # all upcoming = waiting

        out.append({
            "time":    time_str,
            "date":    start_dt[:10],
            "pet":     pet_name or "ไม่ระบุ",
            "type":    pet_type,
            "service": service or "ตรวจทั่วไป",
            "vet":     vet or "ไม่ระบุ",
            "status":  status,
            "_raw":    item,
        })

    # Sort by date then time
    out.sort(key=lambda x: (x.get("date",""), x.get("time","")))
    return out


def map_stock(rows: list, expiry_list: list = None) -> list:
    """
    DRX stock item (from op=520) keys:
      stockUID, stockId, stockName, stockTypeId,
      stockNumber (qty), totalStockRemaining,
      stockNumberAlert (min), stockSalePrice,
      stockStatus, stockExpireStatus, stockNumberUnit
    """
    STOCK_TYPE_MAP = {
        1: "ยา", 2: "เวชภัณฑ์", 3: "อุปกรณ์", 4: "อาหาร",
        5: "วัคซีน", 6: "อื่นๆ", 7: "Lab", 8: "บริการ",
    }
    out = []

    for row in rows:
        if not row or not isinstance(row, dict):
            continue

        uid   = row.get("stockUID", "")
        code  = row.get("stockId", "") or str(uid)
        name  = row.get("stockName", "") or "ไม่ระบุ"
        cat   = STOCK_TYPE_MAP.get(row.get("stockTypeId", 0), "อื่นๆ")
        qty   = float(row.get("totalStockRemaining") or row.get("stockNumber") or 0)
        alert = float(row.get("stockNumberAlert") or 0)
        mx    = max(alert * 5, qty * 1.5, 10)   # estimate max
        cost  = float(row.get("stockSalePrice") or 0)
        unit  = row.get("stockNumberUnit", "")

        # Expiry status
        exp_status = row.get("stockExpireStatus", 0)
        stock_status = row.get("stockStatus", 0)

        if qty <= 0 or stock_status == 0:
            level = "out"
        elif qty <= alert or exp_status in (1, 2):
            level = "low"
        else:
            level = "normal"

        # กรองบริการล้วน (stockTypeId=8) ออก — แสดงเฉพาะ physical items
        if cat == "บริการ":
            continue

        out.append({
            "code":  code,
            "name":  name,
            "cat":   cat,
            "qty":   int(qty),
            "max":   int(mx),
            "unit":  unit,
            "exp":   "-",   # expiry date not in list view
            "cost":  cost,
            "level": level,
        })

    # เรียง: low → out → normal (ของที่ต้องสั่งเพิ่มขึ้นก่อน)
    level_order = {"low": 0, "out": 1, "normal": 2}
    out.sort(key=lambda x: (level_order.get(x["level"], 3), x["name"]))
    return out


def map_transactions(finance_monthly: dict, finance_summary: dict) -> list:
    """
    DRX finance monthly (op=303) response:
    {"barChartDataItems": {"datasets": [
      {"label": "ค่าใช้จ่าย OPD", "data": [0,0,37,241,137,0,...12 months]},
      {"label": "ค่าใช้จ่าย Admit", "data": [...]},
      ...
    ]}, "yearList": ["2026"]}

    แปลงเป็น transactions รายเดือน (สะสมทุก category ต่อเดือน)
    """
    THAI_MONTHS_SHORT = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                         "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    year = datetime.now().year
    out = []
    tid = 1

    if isinstance(finance_monthly, dict):
        chart = finance_monthly.get("barChartDataItems", {})
        datasets = chart.get("datasets", [])

        # รวมทุก category ต่อเดือน
        monthly_total = [0.0] * 12
        for ds in datasets:
            data = ds.get("data", [])
            for m_idx, val in enumerate(data[:12]):
                try:
                    monthly_total[m_idx] += float(val or 0)
                except (ValueError, TypeError):
                    pass

        for m_idx, total in enumerate(monthly_total):
            if total > 0:
                month_num = m_idx + 1
                label = f"{THAI_MONTHS_SHORT[month_num]} {year + 543}"
                out.append({
                    "id":      f"TXN-{tid:04d}",
                    "tid":     tid,
                    "date":    label,
                    "item":    f"รายรับ {label}",
                    "type":    "in",
                    "channel": "OPD+Admit+บริการ",
                    "amount":  round(total, 2),
                })
                tid += 1

    return out


# ===== MAIN =====

def main():
    print("=" * 55)
    print("  DRX Bridge v2 - Dog and Cat Lovely")
    print("=" * 55)

    session = make_session()

    ok = login(session)
    if not ok:
        print("\n[FAIL] Login failed - check:")
        print("  1. DRX system is running and reachable")
        print("  2. Username / Password correct")
        print("  3. Network / VPN")
        sys.exit(1)

    print()
    result = {
        "_meta": {
            "fetched_at": datetime.now().isoformat(),
            "source":     BASE,
            "user":       USERNAME,
            "version":    "2.0",
        }
    }

    def step(name: str, fn, *a, **kw):
        print(f"  Fetching {name:25s}", end=" ", flush=True)
        try:
            data = fn(session, *a, **kw)
            size = len(json.dumps(data, ensure_ascii=False))
            print(f"[OK]  {size:>7,} bytes")
            return data
        except Exception as e:
            print(f"[ERR] {e}")
            return None

    # --- Raw data ---
    raw_summary      = step("summary stats",        fetch_summary)
    raw_cases        = step("cases (OPD)",           fetch_cases,         rows=200)
    raw_opd_active   = step("OPD active today",      fetch_opd_active)
    raw_admit        = step("Admit (overnight)",     fetch_admit_active)
    raw_grooming     = step("Grooming today",        fetch_grooming_active)
    raw_today_cases  = step("Today cases (all)",     fetch_today_cases)
    raw_appointments = step("appointments (grid)",   fetch_appointments,  rows=200)
    raw_calendar     = step("calendar events",       fetch_calendar)
    raw_stock        = step("stock list (paged)",    fetch_stock, max_pages=3)
    raw_stock_exp    = []  # included in fetch_stock stockExpireStatus
    raw_daily_rev    = step("daily revenue (today)", fetch_daily_revenue)
    raw_finance_mo   = step("finance monthly",       fetch_finance_monthly)
    raw_finance_sum  = step("finance summary",       fetch_finance_summary)
    raw_vets         = step("vets list",             fetch_vets)
    raw_customers    = step("customers summary",     fetch_customers_summary)

    print()
    print("  Mapping data...")

    # --- Mapped data ---
    cases_mapped  = map_cases(raw_cases or [])
    appt_mapped   = map_appointments(raw_appointments or [], raw_calendar or [])
    stock_mapped  = map_stock(raw_stock or [], [])
    txn_mapped    = map_transactions(raw_finance_mo or {}, raw_finance_sum or {})

    # Map daily revenue
    daily_rev_mapped = {}
    if isinstance(raw_daily_rev, dict) and raw_daily_rev:
        daily_rev_mapped = {
            "date":           raw_daily_rev.get("currentDateTimeDisplay", ""),
            "received_total": raw_daily_rev.get("sumDailyAllReceivedMoney", 0),   # ← รายรับวันนี้
            "bill_total":     raw_daily_rev.get("totalBillPriceAmount", 0),
            "net_revenue":    raw_daily_rev.get("dailyNetRevenue", 0),
            "total_cost":     raw_daily_rev.get("totalCost", 0),
            "bill_count":     raw_daily_rev.get("numberOfBill", 0),
            "voided_count":   raw_daily_rev.get("numberOfVoidedBill", 0),
            "cash":           raw_daily_rev.get("sumCash", 0),
            "transfer":       raw_daily_rev.get("sumMoneyTransfer", 0),
            "credit":         raw_daily_rev.get("sumCredit", 0),
            "debit":          raw_daily_rev.get("sumDebit", 0),
            "deposit":        raw_daily_rev.get("sumDeposit", 0),
            "deposit_transfer": raw_daily_rev.get("sumDepositMoneyTransfer", 0),
            "first_bill_id":  raw_daily_rev.get("firstBillId", ""),
            "last_bill_id":   raw_daily_rev.get("lastBillId", ""),
            "categories":     raw_daily_rev.get("pieChartDataItems", {}).get("data", []),
        }

    result.update({
        # --- Frontend-ready (mapped) ---
        "cases":        cases_mapped,
        "appointments": appt_mapped,
        "stock":        stock_mapped,
        "transactions": txn_mapped,
        "daily_revenue": daily_rev_mapped,   # ← ข้อมูลรายวันใหม่!

        # --- Summary stats ---
        "summary": raw_summary or {},
        "vets":    raw_vets or [],

        # --- Raw (สำหรับ debug และ mapping เพิ่มเติม) ---
        "_raw": {
            "summary":         raw_summary,
            "cases":           raw_cases,
            "opd_active":      raw_opd_active,
            "admit":           raw_admit,
            "grooming":        raw_grooming,
            "today_cases":     raw_today_cases,
            "appointments":    raw_appointments,
            "calendar":        raw_calendar,
            "stock":           raw_stock[:5] if raw_stock else [],
            "daily_revenue":   raw_daily_rev,
            "finance_monthly": raw_finance_mo,
            "finance_summary": raw_finance_sum,
            "vets":            raw_vets,
            "customers":       raw_customers,
        },
    })

    # บันทึก
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"[SAVED] {OUTPUT}")
    print(f"  - cases:        {len(cases_mapped)} records")
    print(f"  - appointments: {len(appt_mapped)} records")
    print(f"  - stock:        {len(stock_mapped)} records")
    print(f"  - transactions: {len(txn_mapped)} records")
    if daily_rev_mapped:
        print(f"  - daily revenue: {daily_rev_mapped.get('received_total',0):,.0f} baht "
              f"({daily_rev_mapped.get('bill_count',0)} bills)")
    print()

    # Debug: show raw sample
    if raw_cases:
        print("  Sample case row (raw):")
        first = raw_cases[0] if raw_cases else {}
        print(f"    keys: {list(first.keys()) if isinstance(first, dict) else type(first)}")
        print(f"    {json.dumps(first, ensure_ascii=False)[:400]}")
    if raw_appointments:
        print("  Sample appointment row (raw):")
        first = raw_appointments[0] if raw_appointments else {}
        print(f"    keys: {list(first.keys()) if isinstance(first, dict) else type(first)}")
        print(f"    {json.dumps(first, ensure_ascii=False)[:400]}")
    if raw_stock:
        print("  Sample stock row (raw):")
        first = raw_stock[0] if raw_stock else {}
        print(f"    keys: {list(first.keys()) if isinstance(first, dict) else type(first)}")
        print(f"    {json.dumps(first, ensure_ascii=False)[:400]}")

    print()
    print("[DONE] Open vethospital_system.html to view data")


def run_fetch() -> bool:
    """Run DRX fetch — return True if success. ใช้โดย scheduler"""
    try:
        main()
        return OUTPUT.exists()
    except SystemExit:
        return OUTPUT.exists()
    except Exception as e:
        print(f"[ERROR] run_fetch: {e}")
        return False


if __name__ == "__main__":
    main()
