"""
Petbook Snapshot — สร้างฐานข้อมูลก้อนเล็กสำหรับ "สมุดประจำตัว" บน cloud
=================================================================================
ทำไมต้องมี:
    ฐาน drx_opd.db ตัวเต็มอยู่ที่เครื่องคลินิกเท่านั้น พอเครื่องปิดตอนกลางคืน
    ลูกค้าจะเปิดสมุดไม่ได้ — ไฟล์นี้ตัดเฉพาะ "ข้อมูลที่จำเป็นของ HN ที่ลงทะเบียน LINE ไว้"
    ออกมาเป็นไฟล์เล็กๆ ส่งขึ้น Railway ให้เสิร์ฟแทนได้ 24 ชม.

หลักการตัดข้อมูล (ยิ่งน้อยยิ่งดี — เป็นข้อมูลสุขภาพ):
    • เฉพาะ HN ที่มีคนลงทะเบียนรับข่าวสารทาง LINE ไว้เท่านั้น (ไม่ใช่คนไข้ทั้งฐาน)
    • เฉพาะฟิลด์ที่หน้าสมุดใช้จริง — ไม่มีชื่อเจ้าของ เบอร์โทร ที่อยู่
      ไม่มีบันทึกการวินิจฉัย/การรักษาของสัตวแพทย์ (ซึ่งเป็นข้อความภายใน)
    • รูปโปรไฟล์สัตว์ฝังเป็น BLOB (ไฟล์เล็ก) — ส่วนรูปการรักษาไม่ส่งขึ้น cloud

ชื่อตารางตรงกับ drx_opd.db ทุกตัว → customer_history.py ใช้ได้เลยโดยไม่ต้องแก้ query

ใช้งาน:
    python petbook_snapshot.py            # สร้างไฟล์ petbook_cloud.db
    python petbook_snapshot.py --upload   # สร้างแล้วส่งขึ้น cloud ด้วย
"""
import os
import sqlite3
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

SRC_DB = Path(__file__).parent / "drx_opd.db"
OUT_DB = Path(__file__).parent / "petbook_cloud.db"
PICTURE_ROOT = Path(r"D:\DoctorDogs\Pictures")

MAX_PROFILE_PIC_BYTES = 300_000

SCHEMA = """
CREATE TABLE pets (
    uid INTEGER PRIMARY KEY, petid TEXT, petname TEXT, pettype TEXT, petsex TEXT,
    petbreed TEXT, petbirthday TEXT, petstatus TEXT, age_text TEXT
);
CREATE TABLE opd_full (
    opd_id INTEGER PRIMARY KEY, opd_datetime TEXT, hn TEXT, petname TEXT,
    weight_kg REAL, opd_status_name TEXT, item_count INTEGER, total_amount REAL
);
CREATE TABLE opd_items (
    payment_id INTEGER PRIMARY KEY, opd_id INTEGER, item_name TEXT,
    stock_type_id INTEGER, category_name TEXT, qty REAL, net_price REAL
);
CREATE TABLE opd_pictures (opd_picture_id INTEGER PRIMARY KEY, opd_id INTEGER, picture_path TEXT);
CREATE TABLE pet_pictures (pet_uid INTEGER PRIMARY KEY, picture_path TEXT, blob BLOB);
CREATE TABLE appointments (
    appointment_uid INTEGER PRIMARY KEY, appointment_datetime TEXT, status_name TEXT,
    hn TEXT, doctor_name TEXT, come_for_text TEXT, more_info TEXT
);
CREATE TABLE come_for_list (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX idx_opd_full_hn ON opd_full(hn);
CREATE INDEX idx_opd_items_opd ON opd_items(opd_id);
CREATE INDEX idx_appt_hn ON appointments(hn);
CREATE INDEX idx_pets_petid ON pets(petid);
"""


def registered_hns() -> list[str]:
    """HN ที่มี LINE ผูกไว้ — จาก Google Sheet ก่อน แล้ว fallback ไป xlsx"""
    hns: set[str] = set()
    try:
        import gsheet_db
        if gsheet_db.is_enabled():
            hns = {str(r.get("hn") or "").strip() for r in gsheet_db.get_all_customers()}
    except Exception as e:
        print(f"[snapshot] อ่าน Google Sheet ไม่ได้ ({e}) — ใช้ xlsx แทน")
    if not hns:
        try:
            import appointment_db as adb
            hns = {str(c.get("hn") or "").strip() for c in adb.get_all_customers()}
        except Exception as e:
            print(f"[snapshot] อ่านทะเบียนไม่ได้: {e}")
    return sorted(h for h in hns if h)


def _read_picture(picture_path: str) -> bytes | None:
    rel = (picture_path or "").lstrip("/")
    if rel.startswith("images/"):
        rel = rel[len("images/"):]
    fp = PICTURE_ROOT / rel
    try:
        if fp.exists() and fp.stat().st_size <= MAX_PROFILE_PIC_BYTES:
            return fp.read_bytes()
    except OSError:
        pass
    return None


def build(quiet: bool = False) -> dict:
    def log(msg):
        if not quiet:
            print(msg)

    if not SRC_DB.exists():
        raise FileNotFoundError(f"ไม่พบ {SRC_DB.name} — รัน opd_sync.py ก่อน")

    hns = registered_hns()
    if not hns:
        raise RuntimeError("ไม่พบ HN ที่ลงทะเบียนไว้เลย — ยกเลิกการสร้าง snapshot")
    log(f"[snapshot] HN ที่ลงทะเบียน LINE ไว้: {len(hns)} รายการ")

    if OUT_DB.exists():
        OUT_DB.unlink()
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    out = sqlite3.connect(OUT_DB)
    try:
        out.executescript(SCHEMA)
        ph = ",".join("?" * len(hns))

        pets = src.execute(
            f"SELECT uid, petid, petname, pettype, petsex, petbreed, petbirthday, petstatus, age_text "
            f"FROM pets WHERE petid IN ({ph})", hns
        ).fetchall()
        out.executemany("INSERT INTO pets VALUES (?,?,?,?,?,?,?,?,?)", [tuple(r) for r in pets])

        # เอา visit ทั้งหมดของ HN ที่ลงทะเบียน — ห้ามตัดท้าย เพราะประวัติวัคซีนคำนวณจาก
        # ทุกเข็มย้อนหลัง ถ้าตัดออกลูกค้าจะเห็นวัคซีนหายไปเฉยๆ (น้องที่นอน รพ. มีบิลรายวัน
        # เป็นร้อยใบ เคยตัดที่ 60 แล้วเข็มวัคซีนเดือนก่อนๆ หายหมด)
        opd_rows = src.execute(
            f"SELECT opd_id, opd_datetime, hn, petname, weight_kg, opd_status_name, item_count, total_amount "
            f"FROM opd_full WHERE hn IN ({ph})", hns
        ).fetchall()
        out.executemany("INSERT INTO opd_full VALUES (?,?,?,?,?,?,?,?)", [tuple(r) for r in opd_rows])

        opd_ids = [r["opd_id"] for r in opd_rows]
        n_items = 0
        for i in range(0, len(opd_ids), 500):
            chunk = opd_ids[i:i + 500]
            cph = ",".join("?" * len(chunk))
            items = src.execute(
                f"SELECT payment_id, opd_id, item_name, stock_type_id, category_name, qty, net_price "
                f"FROM opd_items WHERE opd_id IN ({cph})", chunk
            ).fetchall()
            out.executemany("INSERT INTO opd_items VALUES (?,?,?,?,?,?,?)", [tuple(r) for r in items])
            n_items += len(items)

        appts = src.execute(
            f"SELECT appointment_uid, appointment_datetime, status_name, hn, doctor_name, come_for_text, more_info "
            f"FROM appointments WHERE hn IN ({ph})", hns
        ).fetchall()
        out.executemany("INSERT INTO appointments VALUES (?,?,?,?,?,?,?)", [tuple(r) for r in appts])

        come_for = src.execute("SELECT id, name FROM come_for_list").fetchall()
        out.executemany("INSERT INTO come_for_list VALUES (?,?)", [tuple(r) for r in come_for])

        # รูปโปรไฟล์ฝังไปด้วย (เครื่องปิดแล้วยังเห็นรูปน้อง) — รูปการรักษาไม่ส่งขึ้น cloud
        pet_uids = [r["uid"] for r in pets]
        n_pics = 0
        if pet_uids:
            uph = ",".join("?" * len(pet_uids))
            for r in src.execute(
                f"SELECT pet_uid, picture_path FROM pet_pictures WHERE pet_uid IN ({uph})", pet_uids
            ):
                blob = _read_picture(r["picture_path"])
                if blob:
                    out.execute("INSERT INTO pet_pictures VALUES (?,?,?)",
                                (r["pet_uid"], r["picture_path"], blob))
                    n_pics += 1

        for r in src.execute("SELECT key, value FROM sync_meta"):
            out.execute("INSERT INTO sync_meta VALUES (?,?)", (r["key"], r["value"]))
        from datetime import datetime
        out.execute("INSERT OR REPLACE INTO sync_meta VALUES ('snapshot_at', ?)",
                    (datetime.now().isoformat(timespec="seconds"),))
        out.commit()
        out.execute("VACUUM")
        out.commit()
    finally:
        src.close()
        out.close()

    size_mb = OUT_DB.stat().st_size / 1_048_576
    result = {"hns": len(hns), "pets": len(pets), "visits": len(opd_rows), "items": n_items,
              "appointments": len(appts), "pet_photos": n_pics, "size_mb": round(size_mb, 2)}
    log(f"[snapshot] สร้าง {OUT_DB.name} สำเร็จ — สัตว์ {result['pets']} ตัว, "
        f"visit {result['visits']}, รายการ {result['items']}, นัด {result['appointments']}, "
        f"รูปโปรไฟล์ {result['pet_photos']} รูป, ขนาด {result['size_mb']} MB")
    return result


def upload(quiet: bool = False) -> bool:
    """ส่งไฟล์ snapshot ขึ้น cloud (Railway) ผ่าน endpoint ที่ป้องกันด้วย INTERNAL_API_KEY"""
    import requests
    url = (os.environ.get("RAILWAY_BOT_URL", "") or "").rstrip("/")
    key = os.environ.get("INTERNAL_API_KEY", "")
    if not url:
        print("[snapshot] ยังไม่ได้ตั้ง RAILWAY_BOT_URL — ข้ามการอัปโหลด")
        return False
    if not OUT_DB.exists():
        print("[snapshot] ยังไม่มีไฟล์ snapshot — สร้างก่อน")
        return False
    try:
        with OUT_DB.open("rb") as f:
            r = requests.post(
                f"{url}/api/petbook_snapshot",
                headers={"X-API-Key": key, "Content-Type": "application/octet-stream"},
                data=f, timeout=180,
            )
        ok = r.status_code == 200
        if not quiet or not ok:
            print(f"[snapshot] อัปโหลด → {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[snapshot] อัปโหลดไม่สำเร็จ: {e}")
        return False


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    build(quiet=quiet)
    if "--upload" in sys.argv:
        upload(quiet=quiet)
