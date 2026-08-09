"""
OPD Sync — ดึง "ทุกประวัติ OPD" (ทุก visit ของสัตว์ป่วยทั้งหมด) จาก DRX MySQL
มาเก็บใน local SQLite (drx_opd.db) เพื่อให้ dashboard / งานวิเคราะห์ / บอทในอนาคต
query ได้เร็ว โดยไม่ต้องยิง query ใส่ฐาน production ทุกครั้ง

ต่างจาก drx_bridge.py (ซึ่งดึงเฉพาะเคสล่าสุดจากหน้าเว็บ ~200 รายการ) — ไฟล์นี้ดึง
ตรงจากตาราง opd ทั้งหมดในฐานข้อมูล ดังนั้นได้ครบทุกประวัติจริง

กลยุทธ์: incremental sync — ตาราง opd / opd_payment_item (ต้นทางของ opd_payment_summary
+ category_daily) / appointment มี add/modify_datetime ที่เชื่อถือได้ (เช็คแล้วไม่มี NULL)
จึงดึงเฉพาะแถวที่เปลี่ยนตั้งแต่ sync ครั้งก่อน (>=watermark) แล้ว UPSERT — เร็วกว่า full
refresh มากเมื่อข้อมูลสะสมเยอะขึ้นเรื่อยๆ
ส่วน customers/pets/stock_items ไม่มี modify_datetime ที่เชื่อถือได้ (โดยเฉพาะ pet ไม่มี
คอลัมน์นี้เลย และ stock ยอดคงเหลือเป็นค่าปัจจุบันเสมอ ไม่ใช่ log การเปลี่ยนแปลง) จึง full
replace ทุกครั้ง แต่ตารางเล็ก (<3,000 แถวรวมกัน) เร็วอยู่แล้ว

sync ครั้งแรก (ยังไม่มี watermark ใน sync_meta) จะ full sync เสมอโดยอัตโนมัติ

ใช้งาน:
    python opd_sync.py            # sync (incremental ถ้าเคย sync มาก่อน) แล้วพิมพ์สรุป
    python opd_sync.py --quiet    # เหมือนกันแต่ไม่พิมพ์ (ไว้เรียกจาก scheduler/Flask)
    python opd_sync.py --full     # บังคับ full refresh ทุกตาราง (ไว้กู้ข้อมูลถ้าสงสัยว่า
                                   # incremental พลาดอะไรไป เช่นแถวเก่าที่ modify_datetime
                                   # ไม่ได้ถูกอัปเดตตอนแก้ไขจริงในบางกรณี)
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from drx_db import fetch_all

DB_PATH = Path(__file__).parent / "drx_opd.db"

# ค่าเริ่มต้นถ้ายังไม่เคย sync มาก่อน — เก่าพอที่จะดึงทุกแถวในการ sync ครั้งแรก
EPOCH = "2000-01-01 00:00:00"

# Schema แบบ idempotent (IF NOT EXISTS ทั้งหมด) เพราะ sync ปกติไม่ต้อง DROP ตารางอีกต่อไป
# (ของเดิม DROP+CREATE ทุกครั้ง ใช้ไม่ได้กับ incremental sync เพราะจะลบข้อมูลที่สะสมไว้ทิ้ง)
SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    uid           INTEGER PRIMARY KEY,
    customerid    TEXT,
    full_name     TEXT,
    tel           TEXT,
    address       TEXT,
    province      TEXT
);
CREATE TABLE IF NOT EXISTS pets (
    uid           INTEGER PRIMARY KEY,
    cuid          INTEGER,
    petid         TEXT,
    petname       TEXT,
    pettype       TEXT,
    petsex        TEXT,
    petbreed      TEXT,
    petbirthday   TEXT,
    petstatus     TEXT,
    age_text      TEXT
);
CREATE TABLE IF NOT EXISTS opd (
    opd_id                  INTEGER PRIMARY KEY,
    pet_uid                 INTEGER,
    opd_datetime            TEXT,
    opd_add_datetime        TEXT,
    opd_modify_datetime     TEXT,
    doctor_id               INTEGER,
    doctor_name             TEXT,
    logged_in_user_id       INTEGER,
    recorded_by_name        TEXT,
    room_name               TEXT,
    temperature_c           REAL,
    pulse                   REAL,
    respiration             REAL,
    weight_kg               REAL,
    opd_status_id           INTEGER,
    opd_status_name         TEXT,
    chief_complaint         TEXT,
    history                 TEXT,
    physical_exam           TEXT,
    dx                      TEXT,
    final_diagnosis         TEXT,
    treatment               TEXT,
    major_problem           TEXT,
    tentative_diagnosis     TEXT,
    client_education        TEXT,
    diagnosis_system_id     INTEGER,
    diagnosis_system_name   TEXT,
    department_id           INTEGER,
    department_name         TEXT
);
CREATE TABLE IF NOT EXISTS opd_payment_summary (
    opd_id       INTEGER PRIMARY KEY,
    item_count   INTEGER,
    total_amount REAL
);
CREATE TABLE IF NOT EXISTS category_daily (
    opd_date       TEXT,
    stock_type_id  INTEGER,
    category_name  TEXT,
    revenue        REAL,
    item_count     INTEGER,
    PRIMARY KEY (opd_date, stock_type_id)
);
-- รายรับรายวันแบบ "รายงานเงินสด" ของ DRX จริง (จาก bill_history = ใบเสร็จที่รับเงินจริง)
-- ต่างจาก category_daily ที่รวมค่ารักษาตามวัน visit — อันนี้รวมตามวันที่รับเงินที่แคชเชียร์
-- net_total = บริการ(type1) + มัดจำ(type2) - คืนเงิน/คืนสินค้า(type3,4) → ตรงกับ "ยอดรายรับสุทธิ รวม" ในรายงาน DRX
CREATE TABLE IF NOT EXISTS bill_daily (
    bill_date       TEXT PRIMARY KEY,
    receipt_count   INTEGER,
    service_total   REAL,   -- type=1 รับเงินค่าบริการ/สินค้า
    deposit_total   REAL,   -- type=2 เงินมัดจำ
    refund_total    REAL,   -- type=3 คืนสินค้า + type=4 คืนเงิน
    cash_total      REAL,   -- เงินสด (type 1,2)
    transfer_total  REAL,   -- โอนเงิน (type 1,2)
    other_total     REAL,   -- เครดิต/เดบิต/เช็ค (type 1,2)
    net_total       REAL    -- service + deposit - refund
);
CREATE TABLE IF NOT EXISTS stock_items (
    uid              INTEGER PRIMARY KEY,
    stock_id         TEXT,
    stock_name       TEXT,
    stock_type_id    INTEGER,
    category_name    TEXT,
    sale_price       REAL,
    purchase_price   REAL,
    qty              REAL,
    alert_qty        REAL
);
CREATE TABLE IF NOT EXISTS appointments (
    appointment_uid       INTEGER PRIMARY KEY,
    appointment_datetime  TEXT,
    status_id             INTEGER,
    status_name           TEXT,
    pet_uid               INTEGER,
    hn                    TEXT,
    petname               TEXT,
    pettype               TEXT,
    owner_name            TEXT,
    doctor_name           TEXT,
    come_for_text         TEXT,
    more_info             TEXT
);
CREATE TABLE IF NOT EXISTS opd_pictures (
    opd_picture_id  INTEGER PRIMARY KEY,
    opd_id          INTEGER,
    picture_path    TEXT
);
CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_opd_pet_uid ON opd(pet_uid);
CREATE INDEX IF NOT EXISTS idx_opd_datetime ON opd(opd_datetime);
CREATE INDEX IF NOT EXISTS idx_pets_cuid ON pets(cuid);
CREATE INDEX IF NOT EXISTS idx_category_daily_date ON category_daily(opd_date);
CREATE INDEX IF NOT EXISTS idx_bill_daily_date ON bill_daily(bill_date);
CREATE INDEX IF NOT EXISTS idx_stock_alert ON stock_items(alert_qty);
CREATE INDEX IF NOT EXISTS idx_appt_datetime ON appointments(appointment_datetime);
CREATE INDEX IF NOT EXISTS idx_opd_pictures_opd_id ON opd_pictures(opd_id);

CREATE VIEW IF NOT EXISTS opd_full AS
SELECT
    o.opd_id, o.opd_datetime, o.opd_add_datetime, o.opd_modify_datetime,
    p.uid AS pet_uid, p.petid AS hn, p.petname, p.pettype, p.petsex, p.petbreed, p.petbirthday, p.petstatus,
    c.uid AS customer_uid, c.full_name AS owner_name, c.tel AS owner_tel, c.province,
    o.doctor_id, o.doctor_name, o.recorded_by_name, o.room_name,
    o.temperature_c, o.pulse, o.respiration, o.weight_kg,
    o.opd_status_id, o.opd_status_name,
    o.chief_complaint, o.history, o.physical_exam, o.dx, o.final_diagnosis, o.treatment,
    o.major_problem, o.tentative_diagnosis, o.client_education,
    o.diagnosis_system_name, o.department_name,
    COALESCE(ps.item_count, 0)   AS item_count,
    COALESCE(ps.total_amount, 0) AS total_amount
FROM opd o
LEFT JOIN pets p            ON p.uid = o.pet_uid
LEFT JOIN customers c       ON c.uid = p.cuid
LEFT JOIN opd_payment_summary ps ON ps.opd_id = o.opd_id;
"""

_OPD_SELECT = """
    SELECT
        o.opd_id, o.pet_uid, o.opd_datetime, o.opd_add_datetime, o.opd_modify_datetime,
        o.doctor_id, o.doctor_name, o.logged_in_user_id, u.name AS recorded_by_name, o.room_name,
        o.opd_T AS temperature_c, o.opd_P AS pulse, o.opd_R AS respiration, o.opd_weight_kg AS weight_kg,
        o.opd_status AS opd_status_id, ost.status AS opd_status_name,
        o.opd_cc AS chief_complaint, o.opd_ht AS history, o.opd_pe AS physical_exam,
        o.opd_dx AS dx, o.opd_final_diag AS final_diagnosis, o.opd_tx AS treatment,
        o.major_problem, o.tentative_diagnosis, o.opd_client_education AS client_education,
        o.diagnosis_system_id, dsl.system_name AS diagnosis_system_name,
        o.department_id, d.name_th AS department_name
    FROM opd o
    LEFT JOIN opd_status ost ON ost.status_id = o.opd_status
    LEFT JOIN diagnosis_system_list dsl ON dsl.id = o.diagnosis_system_id
    LEFT JOIN department d ON d.id = o.department_id
    LEFT JOIN user u ON u.user_id = o.logged_in_user_id
"""

_OPD_INSERT_SQL = """INSERT OR REPLACE INTO opd (
    opd_id, pet_uid, opd_datetime, opd_add_datetime, opd_modify_datetime,
    doctor_id, doctor_name, logged_in_user_id, recorded_by_name, room_name,
    temperature_c, pulse, respiration, weight_kg,
    opd_status_id, opd_status_name, chief_complaint, history, physical_exam, dx,
    final_diagnosis, treatment, major_problem, tentative_diagnosis, client_education,
    diagnosis_system_id, diagnosis_system_name, department_id, department_name
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

_APPT_SELECT = """
    SELECT a.appointment_uid, a.appointment_from_datetime, a.appointment_add_datetime, a.appointment_modify_datetime,
           a.appointment_status AS status_id, ast.status_name,
           p.uid AS pet_uid, p.petid AS hn, p.petname, p.pettype,
           TRIM(CONCAT(COALESCE(c.firstname,''),' ',COALESCE(c.lastname,''))) AS owner_name,
           a.specific_doctor_name AS doctor_name, a.come_for_list, a.more_info
    FROM appointment a
    LEFT JOIN appointment_status ast ON ast.id = a.appointment_status
    LEFT JOIN pet p ON p.uid = a.pet_uid
    LEFT JOIN customer c ON c.uid = p.cuid
"""

_APPT_INSERT_SQL = """INSERT OR REPLACE INTO appointments (
    appointment_uid, appointment_datetime, status_id, status_name, pet_uid, hn, petname, pettype,
    owner_name, doctor_name, come_for_text, more_info
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""

# opd_picture ไม่มีคอลัมน์เวลาเลย ใช้ opd_picture_id (auto-increment) เป็น watermark แทน
_PICTURE_SELECT = """
    SELECT opd_picture_id, opd_id, picture_path
    FROM opd_picture
    WHERE picture_path IS NOT NULL AND picture_path != ''
"""


def _full_name(row: dict) -> str:
    parts = [row.get("title") or "", row.get("firstname") or "", row.get("lastname") or ""]
    return " ".join(p for p in parts if p).strip()


def _age_text(row: dict) -> str:
    y, m, d = row.get("petageyear"), row.get("petagemonth"), row.get("petageday")
    bits = []
    if y: bits.append(f"{y} ปี")
    if m: bits.append(f"{m} เดือน")
    if not bits and d:
        bits.append(f"{d} วัน")
    return " ".join(bits)


def _opd_row_to_tuple(o: dict) -> tuple:
    return (
        o["opd_id"], o["pet_uid"],
        str(o["opd_datetime"]) if o["opd_datetime"] else None,
        str(o["opd_add_datetime"]) if o["opd_add_datetime"] else None,
        str(o["opd_modify_datetime"]) if o["opd_modify_datetime"] else None,
        o["doctor_id"], o["doctor_name"], o["logged_in_user_id"], o["recorded_by_name"], o["room_name"],
        o["temperature_c"], o["pulse"], o["respiration"], o["weight_kg"],
        o["opd_status_id"], o["opd_status_name"], o["chief_complaint"], o["history"],
        o["physical_exam"], o["dx"], o["final_diagnosis"], o["treatment"],
        o["major_problem"], o["tentative_diagnosis"], o["client_education"],
        o["diagnosis_system_id"], o["diagnosis_system_name"], o["department_id"], o["department_name"],
    )


def _appt_row_to_tuple(a: dict) -> tuple:
    return (
        a["appointment_uid"], str(a["appointment_from_datetime"]) if a["appointment_from_datetime"] else None,
        a["status_id"], a["status_name"], a["pet_uid"], a["hn"], a["petname"], a["pettype"],
        a["owner_name"], a["doctor_name"], a["come_for_list"] or "", a["more_info"] or "",
    )


def _get_watermark(conn: sqlite3.Connection, key: str, default: str = EPOCH) -> str:
    row = conn.execute("SELECT value FROM sync_meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def _set_watermark(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO sync_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _sync_customers_pets_stock(conn: sqlite3.Connection, log) -> tuple[int, int, int]:
    """ไม่มี modify_datetime ที่เชื่อถือได้ (pet ไม่มีเลย, stock เป็นยอดปัจจุบันเสมอ)
    ตารางเล็กพอ (<3,000 แถวรวม) จึง full replace ทุกครั้งโดยไม่ต้อง DROP TABLE"""
    customers = fetch_all("""
        SELECT uid, customerid, title, firstname, lastname, tel_1, mobile_1, address, province
        FROM customer
        WHERE mark_hidden_flag = 0 OR mark_hidden_flag IS NULL
    """)
    pets = fetch_all("""
        SELECT uid, cuid, petid, petname, pettype, petsex, petbreed, petbirthday, petstatus,
               petageyear, petagemonth, petageday
        FROM pet
        WHERE mark_hidden_flag = 0 OR mark_hidden_flag IS NULL
    """)
    # สต็อกจริง — ใช้ total_stock_remaining (ยอดคงเหลือจริงที่คำนวณจากบัตรสต็อก) ไม่ใช่
    # stock.stock_all_remaining เพราะคอลัมน์นั้นเป็น 0 ทุกแถวในฐานนี้ (ไม่ได้ใช้งานจริง)
    stock_items = fetch_all("""
        SELECT s.uid, s.stock_id, s.stock_name, s.stock_type_id, st.typename AS category_name,
               s.stock_sale_price AS sale_price, s.stock_purchase_price AS purchase_price,
               COALESCE(tsr.stock_number, 0) AS qty,
               COALESCE(tsr.stock_number_alert, s.stock_number_alert, 0) AS alert_qty
        FROM stock s
        LEFT JOIN stock_type st ON st.id = s.stock_type_id
        LEFT JOIN total_stock_remaining tsr ON tsr.stock_uid = s.uid
        WHERE s.stock_status = 1
    """)

    conn.execute("DELETE FROM customers")
    conn.executemany(
        "INSERT INTO customers (uid, customerid, full_name, tel, address, province) VALUES (?,?,?,?,?,?)",
        [(c["uid"], c["customerid"], _full_name(c), c.get("mobile_1") or c.get("tel_1"), c.get("address"), c.get("province")) for c in customers],
    )
    conn.execute("DELETE FROM pets")
    conn.executemany(
        "INSERT INTO pets (uid, cuid, petid, petname, pettype, petsex, petbreed, petbirthday, petstatus, age_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(p["uid"], p["cuid"], p["petid"], p["petname"], p["pettype"], p["petsex"], p["petbreed"], p["petbirthday"], p["petstatus"], _age_text(p)) for p in pets],
    )
    conn.execute("DELETE FROM stock_items")
    conn.executemany(
        "INSERT INTO stock_items (uid, stock_id, stock_name, stock_type_id, category_name, sale_price, purchase_price, qty, alert_qty) VALUES (?,?,?,?,?,?,?,?,?)",
        [(
            s["uid"], s["stock_id"], s["stock_name"], s["stock_type_id"], s["category_name"],
            float(s["sale_price"] or 0), float(s["purchase_price"] or 0),
            float(s["qty"] or 0), float(s["alert_qty"] or 0),
        ) for s in stock_items],
    )
    log(f"[opd_sync] customers/pets/stock (full replace เสมอ): customer={len(customers)}, pet={len(pets)}, stock={len(stock_items)}")
    return len(customers), len(pets), len(stock_items)


def _recompute_payments_for(conn: sqlite3.Connection, opd_ids: list[int]) -> int:
    """recompute opd_payment_summary + category_daily เฉพาะ opd_id ที่ระบุ (ใช้ทั้ง full/incremental)"""
    if not opd_ids:
        return 0
    placeholders = ",".join(["%s"] * len(opd_ids))

    fresh_summary = fetch_all(
        f"SELECT opd_id, COUNT(*) AS item_count, SUM(payment_total_net_price) AS total_amount "
        f"FROM opd_payment_item WHERE opd_id IN ({placeholders}) GROUP BY opd_id",
        tuple(opd_ids),
    )
    conn.executemany(
        "INSERT OR REPLACE INTO opd_payment_summary (opd_id, item_count, total_amount) VALUES (?,?,?)",
        [(ps["opd_id"], ps["item_count"], float(ps["total_amount"] or 0)) for ps in fresh_summary],
    )

    date_rows = fetch_all(f"SELECT DISTINCT DATE(opd_datetime) AS d FROM opd WHERE opd_id IN ({placeholders})", tuple(opd_ids))
    affected_dates = [str(r["d"]) for r in date_rows if r["d"]]
    if affected_dates:
        date_ph = ",".join(["%s"] * len(affected_dates))
        fresh_category = fetch_all(
            f"""SELECT DATE(o.opd_datetime) AS opd_date, st.id AS stock_type_id, st.typename AS category_name,
                       SUM(opi.payment_total_net_price) AS revenue, COUNT(*) AS item_count
                FROM opd_payment_item opi
                JOIN opd o ON o.opd_id = opi.opd_id
                LEFT JOIN stock_type st ON st.id = opi.stock_type_id
                WHERE opi.opd_id > 0 AND DATE(o.opd_datetime) IN ({date_ph})
                GROUP BY opd_date, st.id""",
            tuple(affected_dates),
        )
        sqlite_date_ph = ",".join(["?"] * len(affected_dates))
        conn.execute(f"DELETE FROM category_daily WHERE opd_date IN ({sqlite_date_ph})", affected_dates)
        conn.executemany(
            "INSERT INTO category_daily (opd_date, stock_type_id, category_name, revenue, item_count) VALUES (?,?,?,?,?)",
            [(
                str(cd["opd_date"]), cd["stock_type_id"], cd["category_name"] or "ไม่ระบุหมวด",
                float(cd["revenue"] or 0), cd["item_count"],
            ) for cd in fresh_category],
        )
    return len(opd_ids)


def _recompute_bill_daily(conn: sqlite3.Connection) -> int:
    """recompute bill_daily ใหม่ทั้งหมดจาก bill_history (รายงานเงินสด DRX จริง)
    full recompute เสมอ (ตารางเล็ก + bill ถูก void/refund ย้อนหลังได้ ทำให้ยอดวันเก่าเปลี่ยน)
    เฉพาะ bill_history_status = 1 (ok) — ไม่นับใบที่ถูก void"""
    rows = fetch_all("""
        SELECT DATE(bill_history_datetime) AS bill_date,
               COUNT(*) AS receipt_count,
               SUM(CASE WHEN bill_history_type=1 THEN net_total ELSE 0 END) AS service_total,
               SUM(CASE WHEN bill_history_type=2 THEN net_total ELSE 0 END) AS deposit_total,
               SUM(CASE WHEN bill_history_type IN (3,4) THEN net_total ELSE 0 END) AS refund_total,
               SUM(CASE WHEN bill_pay_type_id=1 AND bill_history_type IN (1,2) THEN net_total ELSE 0 END) AS cash_total,
               SUM(CASE WHEN bill_pay_type_id=5 AND bill_history_type IN (1,2) THEN net_total ELSE 0 END) AS transfer_total,
               SUM(CASE WHEN bill_pay_type_id IN (2,3,4) AND bill_history_type IN (1,2) THEN net_total ELSE 0 END) AS other_total
        FROM bill_history
        WHERE bill_history_status = 1 AND bill_history_datetime IS NOT NULL
        GROUP BY bill_date
    """)
    conn.execute("DELETE FROM bill_daily")
    conn.executemany(
        """INSERT INTO bill_daily (bill_date, receipt_count, service_total, deposit_total,
            refund_total, cash_total, transfer_total, other_total, net_total)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        [(
            str(r["bill_date"]), r["receipt_count"],
            float(r["service_total"] or 0), float(r["deposit_total"] or 0), float(r["refund_total"] or 0),
            float(r["cash_total"] or 0), float(r["transfer_total"] or 0), float(r["other_total"] or 0),
            float(r["service_total"] or 0) + float(r["deposit_total"] or 0) - float(r["refund_total"] or 0),
        ) for r in rows],
    )
    return len(rows)


def _sync_opd_pictures(conn: sqlite3.Connection) -> int:
    """รูปประกอบการรักษา ผูกกับ opd_id ตรงๆ — ใช้ opd_picture_id (auto-increment) เป็น
    watermark เพราะตารางนี้ไม่มีคอลัมน์เวลาเลย (idempotent: watermark เริ่มที่ 0 ถ้ายังไม่เคย sync
    จึงดึงครบทุกแถวในการ sync ครั้งแรกโดยอัตโนมัติ เหมือนตารางอื่น)"""
    watermark = int(_get_watermark(conn, "last_synced_opd_picture_id", default="0"))
    rows = fetch_all(_PICTURE_SELECT + " AND opd_picture_id > %s ORDER BY opd_picture_id", (watermark,))
    if rows:
        conn.executemany(
            "INSERT OR REPLACE INTO opd_pictures (opd_picture_id, opd_id, picture_path) VALUES (?,?,?)",
            [(r["opd_picture_id"], r["opd_id"], r["picture_path"]) for r in rows],
        )
        _set_watermark(conn, "last_synced_opd_picture_id", str(max(r["opd_picture_id"] for r in rows)))
    return len(rows)


def _full_sync(conn: sqlite3.Connection, log) -> dict:
    log("[opd_sync] full sync (ครั้งแรก หรือถูกสั่ง --full)...")
    counts = _sync_customers_pets_stock(conn, log)

    opd_rows = fetch_all(_OPD_SELECT)
    conn.execute("DELETE FROM opd")
    conn.executemany(_OPD_INSERT_SQL, [_opd_row_to_tuple(o) for o in opd_rows])
    opd_watermark = max(
        (str(o["opd_modify_datetime"] or o["opd_add_datetime"]) for o in opd_rows if (o["opd_modify_datetime"] or o["opd_add_datetime"])),
        default=EPOCH,
    )

    conn.execute("DELETE FROM opd_payment_summary")
    conn.execute("DELETE FROM category_daily")
    all_opd_ids = [o["opd_id"] for o in opd_rows]
    _recompute_payments_for(conn, all_opd_ids)
    payment_watermark = fetch_all(
        "SELECT MAX(COALESCE(payment_item_modify_datetime, payment_item_add_datetime)) AS mx FROM opd_payment_item WHERE opd_id > 0"
    )
    payment_watermark = str(payment_watermark[0]["mx"]) if payment_watermark and payment_watermark[0]["mx"] else EPOCH

    appt_rows = fetch_all(_APPT_SELECT)
    conn.execute("DELETE FROM appointments")
    conn.executemany(_APPT_INSERT_SQL, [_appt_row_to_tuple(a) for a in appt_rows])
    appt_watermark = max(
        (str(a["appointment_modify_datetime"] or a["appointment_add_datetime"]) for a in appt_rows if (a["appointment_modify_datetime"] or a["appointment_add_datetime"])),
        default=EPOCH,
    )

    n_bill_days = _recompute_bill_daily(conn)
    n_pictures = _sync_opd_pictures(conn)

    _set_watermark(conn, "last_synced_opd", opd_watermark)
    _set_watermark(conn, "last_synced_payment", payment_watermark)
    _set_watermark(conn, "last_synced_appt", appt_watermark)

    log(f"[opd_sync] full sync: opd={len(opd_rows)}, appointments={len(appt_rows)}, bill_daily={n_bill_days} วัน, รูป={n_pictures}")
    return {
        "mode": "full",
        "customers": counts[0], "pets": counts[1], "stock_items": counts[2],
        "opd": len(opd_rows), "payment_summary": len(all_opd_ids), "appointments": len(appt_rows),
        "bill_days": n_bill_days, "pictures": n_pictures,
    }


def _incremental_sync(conn: sqlite3.Connection, log) -> dict:
    counts = _sync_customers_pets_stock(conn, log)

    opd_watermark = _get_watermark(conn, "last_synced_opd")
    opd_rows = fetch_all(_OPD_SELECT + " WHERE COALESCE(o.opd_modify_datetime, o.opd_add_datetime) >= %s", (opd_watermark,))
    if opd_rows:
        conn.executemany(_OPD_INSERT_SQL, [_opd_row_to_tuple(o) for o in opd_rows])
        new_opd_watermark = max(str(o["opd_modify_datetime"] or o["opd_add_datetime"]) for o in opd_rows)
        _set_watermark(conn, "last_synced_opd", new_opd_watermark)

    payment_watermark = _get_watermark(conn, "last_synced_payment")
    changed_payment_opd_ids = fetch_all(
        "SELECT DISTINCT opd_id FROM opd_payment_item "
        "WHERE opd_id > 0 AND COALESCE(payment_item_modify_datetime, payment_item_add_datetime) >= %s",
        (payment_watermark,),
    )
    affected_opd_ids = [r["opd_id"] for r in changed_payment_opd_ids]
    n_payment_updated = _recompute_payments_for(conn, affected_opd_ids)
    if affected_opd_ids:
        mx_row = fetch_all(
            "SELECT MAX(COALESCE(payment_item_modify_datetime, payment_item_add_datetime)) AS mx "
            "FROM opd_payment_item WHERE opd_id > 0 AND COALESCE(payment_item_modify_datetime, payment_item_add_datetime) >= %s",
            (payment_watermark,),
        )
        if mx_row and mx_row[0]["mx"]:
            _set_watermark(conn, "last_synced_payment", str(mx_row[0]["mx"]))

    appt_watermark = _get_watermark(conn, "last_synced_appt")
    appt_rows = fetch_all(_APPT_SELECT + " WHERE COALESCE(a.appointment_modify_datetime, a.appointment_add_datetime) >= %s", (appt_watermark,))
    if appt_rows:
        conn.executemany(_APPT_INSERT_SQL, [_appt_row_to_tuple(a) for a in appt_rows])
        new_appt_watermark = max(str(a["appointment_modify_datetime"] or a["appointment_add_datetime"]) for a in appt_rows)
        _set_watermark(conn, "last_synced_appt", new_appt_watermark)

    # bill_daily: recompute เต็มทุกครั้ง (ตารางเล็ก + รองรับ void/refund ย้อนหลังที่เปลี่ยนยอดวันเก่า)
    n_bill_days = _recompute_bill_daily(conn)
    n_pictures = _sync_opd_pictures(conn)

    log(f"[opd_sync] incremental sync: opd ใหม่/แก้ไข={len(opd_rows)}, payment recompute={n_payment_updated} opd_id, "
        f"นัดหมายใหม่/แก้ไข={len(appt_rows)}, bill_daily={n_bill_days} วัน, รูปใหม่={n_pictures}")
    return {
        "mode": "incremental",
        "customers": counts[0], "pets": counts[1], "stock_items": counts[2],
        "opd": len(opd_rows), "payment_summary": n_payment_updated, "appointments": len(appt_rows),
        "bill_days": n_bill_days, "pictures": n_pictures,
    }


def run_sync(quiet: bool = False, full: bool = False) -> dict:
    def log(msg):
        if not quiet:
            print(msg)

    log("[opd_sync] กำลังดึงข้อมูลจาก DRX MySQL (read-only)...")

    # เชื่อมตรงกับไฟล์เดิม (ไม่สร้างไฟล์ temp แล้วสลับ) — ใช้ WAL mode ให้ dashboard ที่กำลัง
    # อ่านอยู่พร้อมกัน (แม้ตอน sync) ไม่ถูกบล็อกและไม่เจอข้อมูลครึ่งๆ กลางๆ ระหว่าง refresh
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.isolation_level = None  # จัดการ transaction เอง (autocommit ปิด, คุมด้วย BEGIN/COMMIT ตรงๆ)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")

        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)

        has_watermark = conn.execute("SELECT 1 FROM sync_meta WHERE key = 'last_synced_opd'").fetchone() is not None
        if full or not has_watermark:
            result = _full_sync(conn, log)
        else:
            result = _incremental_sync(conn, log)

        _set_watermark(conn, "last_synced_at", datetime.now().isoformat(timespec="seconds"))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

    log(f"[opd_sync] เขียนลง {DB_PATH.name} สำเร็จ (โหมด={result['mode']}: opd={result['opd']}, "
        f"payment recompute={result['payment_summary']} opd_id, นัดหมาย={result['appointments']}, "
        f"สัตว์={result['pets']}, เจ้าของ={result['customers']}, สต็อก={result['stock_items']})")

    result["ok"] = True
    result["synced_at"] = datetime.now().isoformat(timespec="seconds")
    return result


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    full = "--full" in sys.argv
    result = run_sync(quiet=quiet, full=full)
    if quiet:
        import json
        print(json.dumps(result, ensure_ascii=False))
