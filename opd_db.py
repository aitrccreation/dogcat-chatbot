"""
OPD Query API — อ่านข้อมูลจาก local SQLite (drx_opd.db) ที่ opd_sync.py สร้างไว้
=================================================================================
เป็นชั้นกลาง (data layer) ที่ทั้ง dashboard (drx_sync_server.py) และบอทในอนาคต
(Bot Lovely) จะเรียกใช้ร่วมกันได้ — ไม่ต้องรู้เรื่อง SQL หรือโครงสร้างตารางเลย

ฟังก์ชันหลัก:
    search_opd(...)     -> ค้นหา/กรองประวัติ OPD (ทุกตัว ทุกช่วงเวลา)
    get_opd_detail(id)  -> รายละเอียด OPD 1 รายการ
    get_pet_history(hn) -> ประวัติ OPD ทั้งหมดของสัตว์ตัวเดียว (ตาม HN)
    get_summary(...)    -> สรุปยอดสำหรับการ์ด/กราฟ (จำนวน visit, รายได้, ต่อเดือน ฯลฯ)
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "drx_opd.db"


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"ไม่พบ {DB_PATH.name} — รัน `python opd_sync.py` ก่อนเพื่อดึงข้อมูลจาก DRX"
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def last_synced_at() -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM sync_meta WHERE key = 'last_synced_at'").fetchone()
        return row["value"] if row else None


# คอลัมน์สำหรับ "รายการ" (ตาราง/list) เท่านั้น — ไม่รวมข้อความยาว (history/physical_exam/
# treatment/client_education) ที่ทำให้ payload ใหญ่โดยไม่จำเป็น (หน้า list ไม่ได้โชว์อยู่แล้ว)
# ถ้าต้องการข้อมูลเต็มของ 1 รายการ ให้เรียก get_opd_detail(opd_id) แทน
_LIST_COLUMNS = """
    opd_id, opd_datetime, hn, petname, pettype, owner_name,
    chief_complaint, dx, final_diagnosis, major_problem,
    recorded_by_name, opd_status_name, total_amount, item_count
"""


def search_opd(keyword: str = "", date_from: str = "", date_to: str = "",
               pet_uid: int | None = None, limit: int = 5000, offset: int = 0) -> list[dict]:
    """ค้นหาประวัติ OPD (แบบสรุปสำหรับตาราง — ไม่รวมข้อความยาว) — keyword ค้นจากชื่อสัตว์/HN/เจ้าของ/การวินิจฉัย"""
    sql = f"SELECT {_LIST_COLUMNS} FROM opd_full WHERE 1=1"
    params: list = []

    if keyword:
        sql += """ AND (
            hn LIKE ? OR petname LIKE ? OR owner_name LIKE ? OR
            dx LIKE ? OR final_diagnosis LIKE ? OR major_problem LIKE ?
        )"""
        like = f"%{keyword}%"
        params += [like, like, like, like, like, like]
    if date_from:
        sql += " AND opd_datetime >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND opd_datetime <= ?"
        params.append(date_to + " 23:59:59")
    if pet_uid is not None:
        sql += " AND pet_uid = ?"
        params.append(pet_uid)

    sql += " ORDER BY opd_datetime DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_opd_detail(opd_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM opd_full WHERE opd_id = ?", (opd_id,)).fetchone()
        return dict(row) if row else None


def get_pet_history(hn: str) -> list[dict]:
    """ทุก OPD ของสัตว์ตัวเดียว เรียงเก่า -> ใหม่ (ไว้ดู timeline การรักษา)"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM opd_full WHERE hn = ? ORDER BY opd_datetime ASC", (hn,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_opd_picture_ids(opd_id: int) -> list[int]:
    """opd_picture_id ของรูปประกอบ visit หนึ่งๆ เรียงตามลำดับที่ถ่าย (ใช้สร้างลิงก์รูปทีหลัง)"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT opd_picture_id FROM opd_pictures WHERE opd_id = ? ORDER BY opd_picture_id",
            (opd_id,),
        ).fetchall()
        return [r["opd_picture_id"] for r in rows]


def get_pet_profile(hn: str) -> dict | None:
    """โปรไฟล์สัตว์ 1 ตัวจาก HN — {hn, pet_name, pet_type, owner_name, phone}
    ใช้ตอนลงทะเบียนเพื่อเติมช่องที่ลูกค้าไม่ได้บอกมา
    """
    hn = (hn or "").strip()
    if not hn:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT p.petid AS hn, p.petname AS pet_name, p.pettype AS pet_type, "
            "       c.full_name AS owner_name, c.tel AS phone "
            "FROM pets p LEFT JOIN customers c ON p.cuid = c.uid "
            "WHERE p.petid = ? LIMIT 1", (hn,)
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT hn, petname AS pet_name, pettype AS pet_type, "
                "       owner_name, '' AS phone "
                "FROM opd_full WHERE hn = ? ORDER BY opd_datetime DESC LIMIT 1", (hn,)
            ).fetchone()
        return dict(row) if row else None


def daily_series(days: int = 30) -> list[dict]:
    """รายได้/จำนวน visit รายวัน ย้อนหลัง N วัน — เติมวันที่ไม่มีข้อมูลด้วย 0

    รายรับ (total_revenue) มาจาก bill_daily = "รายงานเงินสด" ของ DRX จริง (ตามวันที่รับเงินที่แคชเชียร์)
    ให้ตรงกับตัวเลขที่คลินิกเห็นในโปรแกรม DRX เป๊ะ — net = บริการ + มัดจำ - คืนเงิน
    ส่วน drug_revenue/service_revenue เป็นการแยก "ค่ายา vs ค่าบริการ" ตาม stock_type (ตามวัน visit)
    ไว้ดูสัดส่วนเชิงวิเคราะห์ — ยอดรวมของสองอันนี้อาจไม่เท่า total_revenue เพราะคนละมุมมอง
    (visit vs วันรับเงิน, รวม/ไม่รวมมัดจำ) จึงไม่เอามาบวกกันเป็น total"""
    from datetime import date, timedelta

    end = date.today()
    start = end - timedelta(days=days - 1)

    with _connect() as conn:
        visits_by_day = {r["d"]: r["visits"] for r in conn.execute(
            "SELECT substr(opd_datetime,1,10) AS d, COUNT(*) AS visits FROM opd_full "
            "WHERE substr(opd_datetime,1,10) >= ? GROUP BY d",
            (start.isoformat(),),
        ).fetchall()}
        bill_by_day = {r["bill_date"]: dict(r) for r in conn.execute(
            "SELECT * FROM bill_daily WHERE bill_date >= ?", (start.isoformat(),)
        ).fetchall()}
        cat_rows = conn.execute(
            "SELECT opd_date, stock_type_id, SUM(revenue) AS revenue FROM category_daily "
            "WHERE opd_date >= ? GROUP BY opd_date, stock_type_id",
            (start.isoformat(),),
        ).fetchall()

    # stock_type_id = 1 คือ "รายการยา" (Drugs) ตามตาราง stock_type ของ DRX — ที่เหลือถือเป็นบริการ/อื่นๆ
    drug_by_day: dict = {}
    service_by_day: dict = {}
    for r in cat_rows:
        bucket = drug_by_day if r["stock_type_id"] == 1 else service_by_day
        bucket[r["opd_date"]] = bucket.get(r["opd_date"], 0) + (r["revenue"] or 0)

    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        b = bill_by_day.get(d)
        out.append({
            "date": d,
            "visits": visits_by_day.get(d, 0),
            # รายรับจริงตามรายงานเงินสด DRX (bill_history)
            "total_revenue":  round(b["net_total"], 2)      if b else 0,
            "bill_service":   round(b["service_total"], 2)  if b else 0,
            "bill_deposit":   round(b["deposit_total"], 2)  if b else 0,
            "bill_refund":    round(b["refund_total"], 2)   if b else 0,
            "bill_cash":      round(b["cash_total"], 2)     if b else 0,
            "bill_transfer":  round(b["transfer_total"], 2) if b else 0,
            "receipt_count":  b["receipt_count"]            if b else 0,
            # แยกค่ายา/บริการตาม stock_type (เชิงวิเคราะห์ ตามวัน visit)
            "drug_revenue":    round(drug_by_day.get(d, 0), 2),
            "service_revenue": round(service_by_day.get(d, 0), 2),
        })
    return out


def category_totals(days: int = 30) -> list[dict]:
    """รายได้รวมแยกตามหมวดบริการ/สินค้าจริง (ยา/ตรวจรักษา/แล็บ/ผ่าตัด/กรูมมิ่ง ฯลฯ)
    ย้อนหลัง N วัน เรียงมากไปน้อย — ใช้แสดง "หมวดรายได้สูงสุด" หน้า Finance"""
    from datetime import date, timedelta
    with _connect() as conn:
        if days and days > 0:
            start = (date.today() - timedelta(days=days - 1)).isoformat()
            rows = conn.execute(
                "SELECT category_name, SUM(revenue) AS revenue FROM category_daily "
                "WHERE opd_date >= ? GROUP BY category_name ORDER BY revenue DESC",
                (start,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT category_name, SUM(revenue) AS revenue FROM category_daily "
                "GROUP BY category_name ORDER BY revenue DESC"
            ).fetchall()
    return [dict(r) for r in rows]


def top_pets(limit: int = 10) -> list[dict]:
    """สัตว์ที่มาบ่อยที่สุด (จำนวน visit เยอะสุด) — ไว้ดูเคสเรื้อรัง/ลูกค้าประจำ"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT pet_uid, hn, petname, pettype, owner_name, COUNT(*) AS visit_count, "
            "SUM(total_amount) AS total_spent, MAX(opd_datetime) AS last_visit "
            "FROM opd_full GROUP BY pet_uid ORDER BY visit_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_stock_items(tracked_only: bool = True, limit: int = 1000) -> list[dict]:
    """รายการสต็อกจริง — qty/alert_qty มาจาก total_stock_remaining (ยอดคงเหลือจริงจากบัตรสต็อก
    ไม่ใช่ stock.stock_all_remaining ซึ่งเป็น 0 ทุกแถวในฐานนี้ — ดู DRX_DATA_DICTIONARY.md)
    tracked_only=True กรองเฉพาะรายการที่คลินิกตั้งค่า alert ไว้จริง (ตัดรายการแล็บ/บริการที่ไม่มีสต็อกจริงออก)
    หมายเหตุ: qty ติดลบได้จริง (คลินิกนี้ไม่ได้บันทึกของเข้าครบทุกครั้ง) — เป็นข้อมูลจริง ไม่ใช่บั๊ก"""
    sql = "SELECT * FROM stock_items"
    params: list = []
    if tracked_only:
        sql += " WHERE alert_qty > 0"
    sql += " ORDER BY stock_name LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    for r in rows:
        qty, alert = r["qty"] or 0, r["alert_qty"] or 0
        r["level"] = "out" if qty <= 0 else ("low" if alert and qty <= alert else "normal")
    return rows


def get_appointments(date_from: str = "", date_to: str = "", limit: int = 500) -> list[dict]:
    """นัดหมายจริงจาก DRX — ไม่ระบุช่วงวันที่ จะคืนเฉพาะวันนี้เป็นต้นไป (นัดที่ยังไม่ผ่าน)
    หมายเหตุ: ไม่เกี่ยวกับระบบแจ้งเตือน LINE (appointment_sync.py) ซึ่งเป็นคนละ pipeline"""
    sql = "SELECT * FROM appointments WHERE 1=1"
    params: list = []
    if date_from:
        sql += " AND appointment_datetime >= ?"
        params.append(date_from)
    else:
        sql += " AND date(appointment_datetime) >= date('now')"
    if date_to:
        sql += " AND appointment_datetime <= ?"
        params.append(date_to + " 23:59:59")
    sql += " ORDER BY appointment_datetime ASC LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_summary() -> dict:
    with _connect() as conn:
        totals = dict(conn.execute(
            "SELECT COUNT(*) AS total_visits, COUNT(DISTINCT pet_uid) AS total_pets, "
            "COUNT(DISTINCT customer_uid) AS total_owners, SUM(total_amount) AS total_revenue "
            "FROM opd_full"
        ).fetchone())

        by_month = [dict(r) for r in conn.execute(
            "SELECT substr(opd_datetime,1,7) AS ym, COUNT(*) AS visits, SUM(total_amount) AS revenue "
            "FROM opd_full GROUP BY ym ORDER BY ym"
        ).fetchall()]

        # หมายเหตุ: GROUP BY ใช้ expression เต็มซ้ำ (ไม่ใช่ชื่อ alias) เพราะ alias
        # ชื่อชนกับคอลัมน์จริงใน opd_full (เช่น pettype, doctor_name) — ถ้า GROUP BY
        # ด้วยชื่อ alias เฉยๆ SQLite จะ resolve ไปที่คอลัมน์จริงแทน ทำให้ group ผิด
        by_species = [dict(r) for r in conn.execute(
            "SELECT COALESCE(NULLIF(pettype,''),'ไม่ระบุ') AS pettype, COUNT(*) AS visits "
            "FROM opd_full GROUP BY COALESCE(NULLIF(pettype,''),'ไม่ระบุ') ORDER BY visits DESC"
        ).fetchall()]

        # คลินิกนี้ไม่ได้กรอกช่อง doctor_id เป็นหลัก ผู้บันทึกจริงอยู่ใน recorded_by_name (logged_in_user_id)
        top_doctors = [dict(r) for r in conn.execute(
            "SELECT COALESCE(NULLIF(recorded_by_name,''),'ไม่ระบุ') AS recorded_by, COUNT(*) AS visits "
            "FROM opd_full GROUP BY COALESCE(NULLIF(recorded_by_name,''),'ไม่ระบุ') ORDER BY visits DESC LIMIT 10"
        ).fetchall()]

        # diagnosis_system ก็ไม่ได้ใช้เป็นหลัก — dx/major_problem เป็น free-text ที่มีข้อมูลจริงมากกว่า
        top_diagnosis = [dict(r) for r in conn.execute(
            "SELECT TRIM(COALESCE(NULLIF(dx,''), major_problem)) AS diagnosis_text, COUNT(*) AS n "
            "FROM opd_full WHERE TRIM(COALESCE(NULLIF(dx,''), major_problem, '')) != '' "
            "GROUP BY TRIM(COALESCE(NULLIF(dx,''), major_problem)) ORDER BY n DESC LIMIT 10"
        ).fetchall()]

    return {
        **totals,
        "by_month": by_month,
        "by_species": by_species,
        "top_doctors": top_doctors,
        "top_diagnosis": top_diagnosis,
        "last_synced_at": last_synced_at(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_summary(), ensure_ascii=False, indent=2))
