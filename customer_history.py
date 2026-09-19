"""
Customer History — "สมุดประจำตัว" ฝั่งลูกค้า (ต่างจาก bot_admin_commands.py ที่เป็นฝั่งแอดมิน)
=================================================================================
ลูกค้ากดปุ่ม "สมุดประจำตัว" ใน LINE OA → เห็นเฉพาะประวัติของ HN ที่ "ตัวเอง" ลงทะเบียนไว้

ทำไมไม่โชว์ "การวินิจฉัย":
    วัดจากข้อมูลจริง 4,258 visit — dx กรอก 3%, chief_complaint 4%, final_diagnosis 0%
    ส่วน treatment (35%) เป็นชวเลขคลินิก เช่น "Tx both FL; nss+chx scrb" ลูกค้าอ่านไม่รู้เรื่อง
    ของที่ครบทุก visit และอ่านรู้เรื่องคือ "รายการบริการ" ในตาราง opd_items (จาก opd_payment_item)
    → สมุดประจำตัวจึงประกอบจากรายการบริการเป็นหลัก

ความปลอดภัย:
    ทุกฟังก์ชันที่คืนข้อมูลคนไข้รับ line_user_id เสมอ และเช็คสิทธิ์ผ่าน is_authorized()
    ก่อนคืนข้อมูล — ดึงประวัติ HN ที่ไม่ได้ลงทะเบียนไว้กับ LINE นั้นไม่ได้
    รูปการรักษาใช้ URL เซ็น HMAC + หมดอายุ (เหมือนฝั่งแอดมิน) กันคนเดา id ไล่ดูรูปคนอื่น

อ่านอย่างเดียว — ไม่มี query เขียนใดๆ
"""
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

# ที่เครื่องคลินิก: ฐานเต็ม drx_opd.db
# บน cloud (Railway): snapshot ก้อนเล็กที่ petbook_snapshot.py ส่งขึ้นมา — ชื่อตารางเหมือนกัน
# ทุกตัว query จึงใช้ร่วมกันได้ ไม่ต้องเขียนโค้ดแยกสองชุด
DB_PATH = Path(__file__).parent / "drx_opd.db"
CLOUD_DB_PATH = Path(os.environ.get("PETBOOK_DB_PATH", "/data/petbook.db"))
_LOCAL_SNAPSHOT = Path(__file__).parent / "petbook_cloud.db"


def _db_path() -> Path:
    """เลือกฐานที่ใช้ได้จริง — ฐานเต็มก่อน แล้วค่อย snapshot (เผื่ออยู่บน cloud)"""
    for p in (DB_PATH, CLOUD_DB_PATH, _LOCAL_SNAPSHOT):
        if p.exists():
            return p
    return DB_PATH

# ระบบ DRX เริ่มเก็บข้อมูลจริงวันนี้ — ประวัติก่อนหน้านี้ไม่มีในระบบ ต้องบอกลูกค้าให้ชัด
DATA_SINCE = "2026-03-03"

THAI_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

PET_TYPE_TH = {"dog": "สุนัข", "cat": "แมว", "others": "อื่นๆ"}
PET_SEX_TH = {"male": "เพศผู้", "female": "เพศเมีย", "none": "ไม่ระบุ"}

# หมวดที่โชว์ชื่อรายการตรงๆ ได้ (ลูกค้าเข้าใจ + เป็นบริการที่จ่ายเงินซื้อ)
_SHOW_NAME_CATEGORIES = {
    "ค่าตรวจรักษา", "ค่า Lab", "ค่าผ่าตัด", "อาบน้ำตัดขน", "ฝากเลี้ยง",
    "ค่าบริการทางการแพทย์", "ค่าบริการอื่นๆ", "สินค้า Pet Shop",
}
# หมวดยา/เวชภัณฑ์ — ยุบรวมเป็นบรรทัดเดียว ไม่โชว์ชื่อยารายตัวให้ลูกค้า
# (กันเข้าใจผิด/ไปหาซื้อยากินเอง — อยากรู้ชื่อยาให้ถามคุณหมอโดยตรง)
# ตั้ง SHOW_DRUG_NAMES = True ถ้าคลินิกอยากให้ลูกค้าเห็นชื่อยาครบเหมือนในใบเสร็จ
SHOW_DRUG_NAMES = False
_GROUP_CATEGORIES = {"รายการยา", "อุปกรณ์และเวชภัณฑ์", "น้ำเกลือ"}

# วัคซีน — จับจากชื่อรายการ (ชื่อในระบบชัดเจนพอที่จะแยกชนิดได้)
_VACCINE_TYPES = [
    ("rabies", "พิษสุนัขบ้า", [r"rabies", r"defensor", r"rabisin", r"พิษสุนัขบ้า"]),
    ("combo_cat", "วัคซีนรวมแมว", [r"วัคซีนรวมแมว", r"วัคซีนแมว", r"fvrcp"]),
    ("combo_dog", "วัคซีนรวมสุนัข", [r"วัคซีนรวม\s*\d", r"วัคซีนสุนัข", r"dhpp", r"vanguard", r"biocan", r"dha2ppi"]),
    ("leukemia", "ลิวคีเมีย", [r"leukemia", r"ลิวคีเมีย"]),
]
_VACCINE_ANY = re.compile(r"วัคซีน|vaccine|rabies|defensor|rabisin|dhpp|vanguard|biocan|leukemia|dha2ppi", re.I)
# รายการขายแบบเหมาจ่าย ไม่ใช่เข็มที่ฉีดจริง (เข็มจริงถูกบันทึกเป็นรายการแยกในบิลเดียวกัน)
_VACCINE_PACKAGE = re.compile(r"แพคเกจ|แพ็คเกจ|แพ็กเกจ|package", re.I)

# สถานะ opd ที่ไม่ใช่ "ครั้งที่มารับบริการ" — เป็นแค่บันทึกเปลี่ยนสถานะภายใน
# (ADMIT/DISCHARGE/HOTEL_IN = เข้า-ออกโรงพยาบาล/โรงแรม, เสียชีวิต = บันทึกการเสียชีวิต)
# ถ้าไม่กรองออก สมุดประจำตัวจะมีการ์ดว่าง 0 บาท เต็มไปหมด และแสดงบันทึกที่ไม่ควรโผล่
_NON_VISIT_STATUSES = {"ADMIT", "DISCHARGE", "HOTEL_IN", "HOTEL_OUT", "เสียชีวิต"}

# วัคซีนประจำปี — ครบกำหนดเข็มถัดไป 1 ปีหลังเข็มล่าสุด
VACCINE_INTERVAL_DAYS = 365
VACCINE_DUE_SOON_DAYS = 30

# ลูกสุนัข/ลูกแมวฉีดเป็น "ชุด" ห่างกัน 3-4 สัปดาห์ ไม่ใช่ปีละครั้ง
# ถ้าเข็ม 2 เข็มหลังห่างกันไม่เกินนี้ = ยังอยู่ระหว่างชุด → ห้ามบอกว่าครบกำหนดปีหน้า
# (บอกผิดแล้วลูกค้าพลาดเข็มกระตุ้น อันตรายกว่าไม่บอกเลย)
VACCINE_SERIES_GAP_DAYS = 60
VACCINE_SERIES_AGE_MONTHS = 5


def _connect() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise FileNotFoundError(f"ไม่พบฐานข้อมูลสมุดประจำตัว ({path}) — รัน `python opd_sync.py` ก่อน")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def thai_date(value, with_year: bool = True) -> str:
    """'2026-09-18 19:34:00' → '18 ก.ย. 2569' (แปลงเป็น พ.ศ. ให้ลูกค้าอ่าน)"""
    d = _as_date(value)
    if not d:
        return "-"
    out = f"{d.day} {THAI_MONTHS[d.month]}"
    return f"{out} {d.year + 543}" if with_year else out


def _fmt_money(n) -> str:
    return f"{round(n or 0):,}"


# ───── สิทธิ์การเข้าถึง ─────
def get_registry_rows(line_user_id: str) -> list[dict]:
    """แถวทะเบียนของ LINE UID นี้ (Google Sheet ก่อน → fallback xlsx)

    ทะเบียนมีทั้ง hn และ pet_name อยู่แล้ว จึงใช้สร้างการ์ดได้แม้อยู่บน Railway
    ที่ไม่มีฐานข้อมูลคนไข้
    """
    if not line_user_id:
        return []
    rows: list[dict] = []
    try:
        import gsheet_db
        if gsheet_db.is_enabled():
            rows = [r for r in gsheet_db.get_all_customers()
                    if str(r.get("line_user_id") or "") == line_user_id]
    except Exception:
        rows = []
    if not rows:
        try:
            import appointment_db as adb
            rows = adb.find_hns_by_user_id(line_user_id)
        except Exception:
            rows = []

    seen, uniq = set(), []
    for r in rows:
        hn = str(r.get("hn") or "").strip()
        if hn and hn not in seen:
            seen.add(hn)
            uniq.append({"hn": hn, "pet_name": str(r.get("pet_name") or "").strip()})
    return uniq


def get_user_hns(line_user_id: str) -> list[str]:
    """HN ทั้งหมดที่ LINE UID นี้ลงทะเบียนไว้"""
    return [r["hn"] for r in get_registry_rows(line_user_id)]


def is_authorized(line_user_id: str, hn: str) -> bool:
    return (hn or "").strip() in get_user_hns(line_user_id)


# ───── โปรไฟล์สัตว์ ─────
def get_pet_profile(hn: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT uid AS pet_uid, petid AS hn, petname, pettype, petsex, petbreed, "
            "       petbirthday, age_text, petstatus "
            "FROM pets WHERE petid = ? LIMIT 1", (hn,)
        ).fetchone()
        if not row:
            return None
        pet = dict(row)
        latest = conn.execute(
            "SELECT weight_kg FROM opd_full WHERE hn = ? AND weight_kg IS NOT NULL "
            "ORDER BY opd_datetime DESC LIMIT 1", (hn,)
        ).fetchone()
        # รูปโปรไฟล์มีแค่ส่วนน้อย (83/1,174 ตัว) — ที่เหลือหน้าเว็บจะวาดรูปหมา/แมวแทน
        has_photo = conn.execute(
            "SELECT 1 FROM pet_pictures WHERE pet_uid = ?", (pet["pet_uid"],)
        ).fetchone() is not None
    pet["pet_type_th"] = PET_TYPE_TH.get(pet["pettype"] or "", "-")
    pet["pet_sex_th"] = PET_SEX_TH.get(pet["petsex"] or "", "-")
    pet["weight_kg"] = latest["weight_kg"] if latest else None
    pet["has_photo"] = has_photo
    return pet


# ───── รายการบริการต่อ visit ─────
def _display_items(rows) -> list[str]:
    """แปลงรายการดิบเป็นบรรทัดที่ลูกค้าอ่านรู้เรื่อง — ยา/เวชภัณฑ์ยุบเป็นบรรทัดเดียว"""
    named: list[str] = []
    grouped = 0
    for r in rows:
        cat = r["category_name"] or ""
        name = (r["item_name"] or "").strip()
        if not name:
            continue
        is_vaccine = bool(_VACCINE_ANY.search(name))
        if is_vaccine or SHOW_DRUG_NAMES or cat in _SHOW_NAME_CATEGORIES:
            if name not in named:
                named.append(name)
        else:
            grouped += 1
    if grouped:
        # visit ที่มีแต่ยา (ไม่มีบริการที่ตั้งชื่อได้เลย) ต้องมีหัวข้อกำกับ ไม่งั้นการ์ดว่างเปล่า
        named.append(f"ตรวจรักษาและให้ยา {grouped} รายการ" if not named
                     else f"ยาและเวชภัณฑ์ {grouped} รายการ")
    return named


def _date_range_th(start, end) -> str:
    """'14–18 ก.ย. 2569' (เดือนเดียวกัน) หรือ '28 ส.ค. – 3 ก.ย. 2569' (ข้ามเดือน)"""
    if not start or not end or start == end:
        return thai_date(end or start)
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.day}–{thai_date(end)}"
    return f"{thai_date(start, with_year=False)} – {thai_date(end)}"


def _merge_episodes(days: list[dict]) -> list[dict]:
    """ยุบ "วันที่มารับบริการติดกัน" เป็นครั้งเดียว แล้วสรุปค่าใช้จ่ายของครั้งนั้น

    น้องที่นอนโรงพยาบาล/ฝากเลี้ยงมีบิลรายวัน ถ้าแยกการ์ดทุกวันจะอ่านยากมาก
    ลูกค้ามองว่าเป็น "การมา 1 ครั้ง" จึงรวมรายการทั้งช่วงแล้วสรุปยอดครั้งเดียว
    days เรียงใหม่ → เก่า"""
    episodes: list[dict] = []
    for d in days:
        prev = episodes[-1] if episodes else None
        prev_start = _as_date(prev["date_start"]) if prev else None
        cur = _as_date(d["date"])
        # ต่อเนื่องกัน = วันก่อนหน้าของช่วงเดิมพอดี (0 = บันทึกหลายใบในวันเดียวกัน)
        if prev_start and cur and (prev_start - cur).days in (0, 1):
            prev["date_start"] = min(prev["date_start"], d["date"])
            prev["_rows"].extend(d["_rows"])
            prev["total"] += d["total"]
            prev["photo_ids"].extend(d["photo_ids"])
            prev["weight_kg"] = prev["weight_kg"] or d["weight_kg"]
            prev["_days"].add(d["date"])
            continue
        episodes.append(d)

    out = []
    for e in episodes:
        services = _display_items(e["_rows"])
        day_count = len(e["_days"])
        if not services:
            services = ["เข้าตรวจติดตามอาการ"]
        out.append({
            "opd_id": e["opd_id"],
            "date": e["date"],
            "date_start": e["date_start"],
            "date_th": _date_range_th(_as_date(e["date_start"]), _as_date(e["date"])),
            "day_count": day_count,
            "services": services,
            "total": e["total"],
            "total_th": _fmt_money(e["total"]),
            "weight_kg": e["weight_kg"],
            "photo_ids": e["photo_ids"],
        })
    return out


def get_visits(hn: str, limit: int = 20, with_photos: bool = True) -> list[dict]:
    """ประวัติการใช้บริการ เรียงใหม่ → เก่า"""
    with _connect() as conn:
        status_ph = ",".join("?" * len(_NON_VISIT_STATUSES))
        # ดึงเผื่อไว้ก่อนยุบวันฝากเลี้ยง ไม่งั้นน้องที่นอนยาวจะกินโควตาจนไม่เหลือ visit อื่น
        visits = conn.execute(
            f"SELECT opd_id, opd_datetime, weight_kg, total_amount, item_count, opd_status_name "
            f"FROM opd_full WHERE hn = ? AND COALESCE(opd_status_name,'') NOT IN ({status_ph}) "
            f"ORDER BY opd_datetime DESC LIMIT ?",
            (hn, *sorted(_NON_VISIT_STATUSES), min(limit * 6, 300)),
        ).fetchall()
        if not visits:
            return []
        ids = [v["opd_id"] for v in visits]
        ph = ",".join("?" * len(ids))
        items_by_opd: dict[int, list] = {}
        for r in conn.execute(
            f"SELECT opd_id, item_name, category_name, net_price FROM opd_items "
            f"WHERE opd_id IN ({ph}) ORDER BY payment_id", ids
        ):
            items_by_opd.setdefault(r["opd_id"], []).append(r)
        pics_by_opd: dict[int, list[int]] = {}
        if with_photos:
            for r in conn.execute(
                f"SELECT opd_id, opd_picture_id FROM opd_pictures "
                f"WHERE opd_id IN ({ph}) ORDER BY opd_picture_id", ids
            ):
                pics_by_opd.setdefault(r["opd_id"], []).append(r["opd_picture_id"])

    days = []
    for v in visits:
        day = str(v["opd_datetime"] or "")[:10]
        days.append({
            "opd_id": v["opd_id"],
            "date": day,
            "date_start": day,
            "_days": {day},
            "_rows": list(items_by_opd.get(v["opd_id"], [])),
            "total": float(v["total_amount"] or 0),
            "weight_kg": v["weight_kg"],
            "photo_ids": list(pics_by_opd.get(v["opd_id"], [])),
        })
    return _merge_episodes(days)[:limit]


def _decode_come_for(conn: sqlite3.Connection, come_for_text: str) -> list[str]:
    """'103,115' → ['วัคซีนพิษสุนัขบ้า', 'วัคซีนไข้หัดและหวัดแมว']"""
    ids = [int(x) for x in re.findall(r"\d+", come_for_text or "")]
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, name FROM come_for_list WHERE id IN ({ph})", ids).fetchall()
    by_id = {r["id"]: r["name"] for r in rows}
    return [by_id[i] for i in ids if by_id.get(i)]


def get_next_appointment(hn: str) -> dict | None:
    """นัดครั้งถัดไปที่ "หมอนัดไว้จริง" ในระบบ DRX — แม่นกว่าการคำนวณวันครบกำหนดเอง
    ข้ามนัดที่ถูกยกเลิก (status_name = ยกเลิกการนัดหมาย)"""
    today = date.today().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT appointment_datetime, status_name, doctor_name, come_for_text, more_info "
            "FROM appointments WHERE hn = ? AND DATE(appointment_datetime) >= ? "
            "AND COALESCE(status_name,'') NOT LIKE '%ยกเลิก%' "
            "ORDER BY appointment_datetime LIMIT 1", (hn, today)
        ).fetchone()
        if not row:
            return None
        reasons = _decode_come_for(conn, row["come_for_text"])
    d = _as_date(row["appointment_datetime"])
    return {
        "date": d.isoformat() if d else "",
        "date_th": thai_date(row["appointment_datetime"]),
        "days_left": (d - date.today()).days if d else None,
        "reasons": reasons,
        "note": (row["more_info"] or "").strip(),
        "doctor": (row["doctor_name"] or "").strip(),
        "is_vaccine": any("วัคซีน" in r for r in reasons),
    }


def get_visit_stats(hn: str) -> dict:
    """สรุปยอดจาก visit "ทั้งหมด" ของ HN (ไม่ใช่แค่ N ครั้งล่าสุดที่ดึงมาแสดง)"""
    with _connect() as conn:
        status_ph = ",".join("?" * len(_NON_VISIT_STATUSES))
        row = conn.execute(
            f"SELECT COUNT(*) AS n, SUM(total_amount) AS spend, "
            f"MIN(opd_datetime) AS first_at, MAX(opd_datetime) AS last_at "
            f"FROM opd_full WHERE hn = ? AND COALESCE(opd_status_name,'') NOT IN ({status_ph})",
            (hn, *sorted(_NON_VISIT_STATUSES)),
        ).fetchone()
    spend = float(row["spend"] or 0)
    return {
        "visit_count": row["n"] or 0,
        "total_spend": spend,
        "total_spend_th": _fmt_money(spend),
        "first_visit_th": thai_date(row["first_at"]) if row["first_at"] else "-",
        "last_visit_th": thai_date(row["last_at"]) if row["last_at"] else "-",
    }


# ───── วัคซีน ─────
def _vaccine_type(name: str):
    low = (name or "").lower()
    for key, label, patterns in _VACCINE_TYPES:
        if any(re.search(p, low, re.I) for p in patterns):
            return key, label
    if _VACCINE_ANY.search(name or ""):
        return "other", "วัคซีนอื่นๆ"
    return None


def get_vaccines(hn: str) -> list[dict]:
    """ประวัติวัคซีนแยกตามชนิด + วันครบกำหนดเข็มถัดไป (เข็มล่าสุด + 1 ปี)"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT i.item_name, i.qty, DATE(o.opd_datetime) AS d FROM opd_items i "
            "JOIN opd_full o ON o.opd_id = i.opd_id WHERE o.hn = ? ORDER BY o.opd_datetime", (hn,)
        ).fetchall()

    by_type: dict[str, dict] = {}
    seen: set[tuple] = set()
    for r in rows:
        name = (r["item_name"] or "").strip()
        # "แพคเกจวัคซีน..." เป็นรายการขายแบบเหมาจ่าย ไม่ใช่เข็มที่ฉีด — ตัวเข็มจริงถูกบันทึก
        # เป็นรายการแยกในบิลเดียวกันอยู่แล้ว ถ้านับด้วยจะได้จำนวนเข็มเกินจริง
        if _VACCINE_PACKAGE.search(name):
            continue
        hit = _vaccine_type(name)
        if not hit:
            continue
        key, label = hit
        d = _as_date(r["d"])
        if not d or (d, name) in seen:
            continue
        seen.add((d, name))
        rec = by_type.setdefault(key, {"key": key, "label": label, "doses": []})
        rec["doses"].append({"date": d, "name": name, "qty": float(r["qty"] or 1)})

    today = date.today()
    age_months = _age_months(hn)
    out = []
    for rec in by_type.values():
        rec["doses"].sort(key=lambda x: x["date"])
        dates = [x["date"] for x in rec["doses"]]
        last = dates[-1]
        # ยังอยู่ระหว่างชุดลูกสัตว์ไหม — ดูจากระยะห่างเข็มท้ายๆ และอายุสัตว์
        gap_recent = (dates[-1] - dates[-2]).days if len(dates) >= 2 else None
        in_series = (
            (gap_recent is not None and gap_recent <= VACCINE_SERIES_GAP_DAYS)
            or (age_months is not None and age_months < VACCINE_SERIES_AGE_MONTHS)
        )
        item = {
            "key": rec["key"], "label": rec["label"], "item_name": rec["doses"][-1]["name"],
            "dose_count": len(rec["doses"]),
            "doses": [{
                "date": x["date"].isoformat(),
                "date_th": thai_date(x["date"]),
                "name": x["name"],
                "qty": x["qty"],
            } for x in reversed(rec["doses"])],
            "last_date": last.isoformat(), "last_date_th": thai_date(last),
            "in_series": in_series,
        }
        if in_series:
            # ไม่ประกาศวันครบกำหนดเอง — เข็มกระตุ้นของลูกสัตว์หมอเป็นคนกำหนด
            item.update({"next_due": "", "next_due_th": "ตามที่สัตวแพทย์นัด",
                         "days_left": None, "status": "in_series"})
        else:
            next_due = last + timedelta(days=VACCINE_INTERVAL_DAYS)
            days_left = (next_due - today).days
            item.update({
                "next_due": next_due.isoformat(), "next_due_th": thai_date(next_due),
                "days_left": days_left,
                "status": "overdue" if days_left < 0 else ("due_soon" if days_left <= VACCINE_DUE_SOON_DAYS else "ok"),
            })
        out.append(item)
    order = {"overdue": 0, "due_soon": 1, "in_series": 2, "ok": 3}
    out.sort(key=lambda v: (order[v["status"]], v["days_left"] if v["days_left"] is not None else 9999))
    return out


def _age_months(hn: str) -> int | None:
    """อายุสัตว์เป็นเดือน จาก petbirthday (คืน None ถ้าไม่ระบุวันเกิด)"""
    with _connect() as conn:
        row = conn.execute("SELECT petbirthday FROM pets WHERE petid = ? LIMIT 1", (hn,)).fetchone()
    if not row:
        return None
    try:
        import opd_sync
        born = opd_sync.parse_thai_date(row["petbirthday"])
    except Exception:
        born = None
    if not born:
        return None
    today = date.today()
    months = (today.year - born.year) * 12 + (today.month - born.month)
    return months - (1 if today.day < born.day else 0)


# ───── payload รวมสำหรับ LIFF / Flex ─────
def has_data() -> bool:
    """มีฐานข้อมูลให้ query ไหม (ฐานเต็มที่คลินิก หรือ snapshot บน cloud)"""
    return _db_path().exists()


def get_pet_list(line_user_id: str) -> list[dict]:
    """สัตว์ทุกตัวที่ LINE นี้ลงทะเบียนไว้ (ไว้ให้เลือกเมื่อมีหลายตัว)

    ทำงานได้ทั้งบนเครื่องคลินิก (มี drx_opd.db → ได้ชื่อน้องด้วย) และบน Railway
    (ไม่มีฐานข้อมูล → ได้แค่ HN จากทะเบียน Google Sheet ซึ่งพอสำหรับสร้างการ์ด)
    """
    rows = get_registry_rows(line_user_id)
    if not has_data():
        # บน Railway — ใช้ชื่อน้องจากทะเบียนแทน (ข้อมูลเต็มอยู่ในหน้าสมุดที่เครื่องคลินิกเสิร์ฟ)
        return [{"hn": r["hn"], "petname": r["pet_name"] or f"HN {r['hn']}",
                 "pettype": None, "visit_count": 0} for r in rows]

    out = []
    for r in rows:
        hn = r["hn"]
        pet = get_pet_profile(hn)
        if not pet:
            out.append({"hn": hn, "petname": r["pet_name"] or f"HN {hn}",
                        "pettype": None, "visit_count": 0})
            continue
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MAX(opd_datetime) AS last FROM opd_full WHERE hn = ?", (hn,)
            ).fetchone()
        out.append({
            "hn": hn, "petname": pet["petname"], "pettype": pet["pettype"],
            "pet_type_th": pet["pet_type_th"], "petbreed": pet["petbreed"],
            "visit_count": row["n"], "last_visit_th": thai_date(row["last"]) if row["last"] else "-",
        })
    return out


def build_history(line_user_id: str, hn: str, limit: int = 20, photo_url_fn=None,
                  pet_photo_url_fn=None) -> dict:
    """payload เดียวที่หน้า LIFF / Flex ใช้ได้ทั้งหมด — เช็คสิทธิ์ก่อนเสมอ"""
    hn = (hn or "").strip()
    if not is_authorized(line_user_id, hn):
        return {"ok": False, "error": "unauthorized",
                "message": "ไม่พบ HN นี้ในทะเบียนของคุณค่ะ — กรุณาลงทะเบียนก่อน"}
    return build_history_unchecked(hn, limit=limit, photo_url_fn=photo_url_fn,
                                   pet_photo_url_fn=pet_photo_url_fn)


def build_history_unchecked(hn: str, limit: int = 20, photo_url_fn=None,
                            pet_photo_url_fn=None) -> dict:
    """เหมือน build_history() แต่ "ไม่เช็คสิทธิ์"

    ใช้ได้เฉพาะเส้นทางที่ยืนยันตัวตนมาแล้วทางอื่น (เช่น preview ฝั่งคลินิกที่ป้องกันด้วย
    INTERNAL_API_KEY) — ห้ามเรียกจาก endpoint ที่ลูกค้าเข้าถึงได้โดยตรงเด็ดขาด
    """
    hn = (hn or "").strip()
    pet = get_pet_profile(hn)
    if not pet:
        return {"ok": False, "error": "not_found",
                "message": f"ยังไม่มีข้อมูลของ HN {hn} ในระบบค่ะ"}

    if pet.get("has_photo") and pet_photo_url_fn:
        pet["photo_url"] = pet_photo_url_fn(pet["pet_uid"])

    # ดึงทั้งหมดก่อน (ยุบวันฝากเลี้ยงแล้ว) เพื่อให้ "มาทั้งหมด N ครั้ง" ตรงกับจำนวนการ์ดที่เห็น
    all_visits = get_visits(hn, limit=999)
    visits = all_visits[:limit]
    if photo_url_fn:
        for v in visits:
            v["photos"] = [u for pid in v["photo_ids"][:6] if (u := photo_url_fn(pid))]

    # สัตว์ที่เสียชีวิตแล้ว — หน้าเป็น "สมุดความทรงจำ" ไม่เตือนวัคซีน ไม่ชวนจองคิว
    is_memorial = (pet.get("petstatus") or "") == "dead"
    vaccines = get_vaccines(hn)
    if is_memorial:
        for v in vaccines:
            v["status"] = "ok"

    stats = get_visit_stats(hn)
    stats["visit_count"] = len(all_visits)

    return {
        "ok": True,
        "hn": hn,
        "pet": pet,
        "is_memorial": is_memorial,
        "stats": stats,
        "visits": visits,
        "vaccines": vaccines,
        "next_appointment": None if is_memorial else get_next_appointment(hn),
        "data_since_th": thai_date(DATA_SINCE),
    }


if __name__ == "__main__":
    import json
    import sys
    hn = sys.argv[1] if len(sys.argv) > 1 else "690765-1"
    print(json.dumps({
        "pet": get_pet_profile(hn),
        "visits": get_visits(hn, limit=5),
        "vaccines": get_vaccines(hn),
    }, ensure_ascii=False, indent=2, default=str))


# ───── preview ฝั่งคลินิก (ไม่ใช่เส้นทางของลูกค้า) ─────
def get_pet_list_demo(limit: int = 6) -> list[dict]:
    """สัตว์ที่มีประวัติเยอะสุด — ไว้ให้คลินิกดูหน้าตาสมุดประจำตัวก่อนเปิดใช้จริง
    เรียกได้เฉพาะ endpoint ที่ป้องกันด้วย INTERNAL_API_KEY เท่านั้น"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT hn, COUNT(*) AS n, MAX(opd_datetime) AS last FROM opd_full "
            "WHERE hn IS NOT NULL GROUP BY hn ORDER BY n DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        pet = get_pet_profile(r["hn"])
        if not pet:
            continue
        out.append({
            "hn": r["hn"], "petname": pet["petname"], "pettype": pet["pettype"],
            "pet_type_th": pet["pet_type_th"], "petbreed": pet["petbreed"],
            "visit_count": r["n"], "last_visit_th": thai_date(r["last"]),
        })
    return out
