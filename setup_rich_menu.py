"""
setup_rich_menu.py — สร้าง LINE Rich Menu 4 ปุ่ม สำหรับ Dog & Cat Lovely
=========================================================================
Layout 2×2 (2500×843):
  [ลงทะเบียนรับนัด]  [จองคิว]
  [ทำหมัน]           [วัคซีน]

Usage:
    python setup_rich_menu.py           # สร้าง + set เป็น default
    python setup_rich_menu.py --list    # แสดงรายการ Rich Menu ที่มีอยู่
    python setup_rich_menu.py --delete  # ลบ Rich Menu ทั้งหมดของ channel
    python setup_rich_menu.py --preview # สร้างรูปอย่างเดียว (richmenu_preview.png)
"""
import io
import os
import sys
from pathlib import Path

# แก้ UnicodeEncodeError บน Windows console
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

import requests

LINE_TOKEN = os.environ.get(
    "LOVELY_BOT_TOKEN",
    os.environ.get("LINE_TOKEN", "")
).strip()

# Rich Menu: half-screen 2×2
W, H = 2500, 843
CLINIC_PHONE = "080-4288181"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ปุ่ม 4 ช่อง
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BUTTONS = [
    # (label, send_text, bg_color, lighter_border, icon_key)
    ("ลงทะเบียนรับนัด", "ลงทะเบียน",  "#059669", "#10B981", "bell"),
    ("จองคิว",          "จองคิว",      "#2563EB", "#3B82F6", "calendar"),
    ("ทำหมัน",          "ทำหมัน",      "#7C3AED", "#8B5CF6", "scissors"),
    ("วัคซีน",          "วัคซีน",      "#0891B2", "#06B6D4", "syringe"),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _find_font(size: int):
    from PIL import ImageFont
    for fp in [
        r"C:\Windows\Fonts\THSarabunNew Bold.ttf",
        r"C:\Windows\Fonts\THSarabunNew.ttf",
        r"C:\Windows\Fonts\NotoSansThai-Bold.ttf",
        r"C:\Windows\Fonts\cordia.ttc",
        r"C:\Windows\Fonts\cordia.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _panel(draw, x0, y0, x1, y1, color, radius=40):
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=color)
    except AttributeError:
        draw.rectangle([x0, y0, x1, y1], fill=color)


def _text(draw, cx, cy, txt, font, color):
    try:
        draw.text((cx, cy), txt, fill=color, font=font, anchor="mm")
    except TypeError:
        try:
            bb = draw.textbbox((0, 0), txt, font=font)
            w, h = bb[2]-bb[0], bb[3]-bb[1]
        except AttributeError:
            w, h = draw.textsize(txt, font=font)
        draw.text((cx - w//2, cy - h//2), txt, fill=color, font=font)


# ── Icons (PIL primitives) ──
def _icon_bell(draw, cx, cy, size=100, c="#FFFFFF", lw=12):
    s = size
    r = s // 2
    bw, bh = int(r*1.4), int(r*1.0)
    ty = cy - r + 8
    by = cy + int(r*0.32)
    draw.line([cx, ty-lw*2, cx, ty+2], fill=c, width=max(lw-4,5))
    draw.arc([cx-bw//2, ty, cx+bw//2, ty+bh*2], 180, 360, fill=c, width=lw)
    st = ty + bh
    draw.line([cx-bw//2, st, cx-bw//2, by], fill=c, width=lw)
    draw.line([cx+bw//2, st, cx+bw//2, by], fill=c, width=lw)
    draw.line([cx-bw//2-lw*2, by, cx+bw//2+lw*2, by], fill=c, width=lw)
    cr = lw+3
    draw.ellipse([cx-cr, by+3, cx+cr, by+3+cr*2], fill=c)


def _icon_calendar(draw, cx, cy, size=100, c="#FFFFFF", lw=12):
    s = size
    r = s // 2
    x0, y0, x1, y1 = cx-r, cy-r+8, cx+r, cy+r-4
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=lw*2, outline=c, width=lw)
    except AttributeError:
        draw.rectangle([x0, y0, x1, y1], outline=c, width=lw)
    hdr_y = y0 + (y1-y0)//3
    draw.line([x0, hdr_y, x1, hdr_y], fill=c, width=lw)
    # grid dots
    cw = (x1-x0) // 4
    ch = (y1-hdr_y) // 3
    for row in range(2):
        for col in range(3):
            dx = x0 + cw*(col+1)
            dy = hdr_y + ch*(row+1)
            dr = lw//2+2
            draw.ellipse([dx-dr, dy-dr, dx+dr, dy+dr], fill=c)
    # ring handles on top
    for hx in [cx - r//2, cx + r//2]:
        draw.line([hx, y0-lw*3, hx, y0+lw], fill=c, width=lw)


def _icon_scissors(draw, cx, cy, size=100, c="#FFFFFF", lw=12):
    s = size
    r = s // 2
    # สองใบมีด: เส้นทแยงสองเส้น พร้อมวงกลมที่ด้าม
    cr = lw + 4
    # ด้ามบนซ้าย
    hx1, hy1 = cx - r//2, cy - r//2
    draw.ellipse([hx1-cr, hy1-cr, hx1+cr, hy1+cr], outline=c, width=lw)
    # ด้ามล่างซ้าย
    hx2, hy2 = cx - r//2, cy + r//2
    draw.ellipse([hx2-cr, hy2-cr, hx2+cr, hy2+cr], outline=c, width=lw)
    # ใบมีดทั้งสอง (เส้นตรงจากด้ามไปปลาย)
    draw.line([hx1, hy1, cx+r, cy-r//4], fill=c, width=lw)
    draw.line([hx2, hy2, cx+r, cy+r//4], fill=c, width=lw)
    # จุดกลาง (pivot)
    pr = lw//2+2
    draw.ellipse([cx-pr, cy-pr, cx+pr, cy+pr], fill=c)


def _icon_syringe(draw, cx, cy, size=100, c="#FFFFFF", lw=12):
    s = size
    r = s // 2
    # ตัวกระบอก (แนวนอน)
    bx0, bx1 = cx - r + lw, cx + r - lw*3
    by0, by1 = cy - lw*3, cy + lw*3
    try:
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=lw, outline=c, width=lw)
    except AttributeError:
        draw.rectangle([bx0, by0, bx1, by1], outline=c, width=lw)
    # เข็ม (ปลาย)
    draw.line([bx1, cy, bx1+lw*4, cy], fill=c, width=lw)
    # ก้านดัน (plunger)
    draw.line([bx0, cy, bx0-lw*3, cy], fill=c, width=lw)
    draw.line([bx0-lw*3, by0, bx0-lw*3, by1], fill=c, width=lw)
    # ขีดตวง
    for frac in [0.33, 0.66]:
        tx = int(bx0 + (bx1-bx0)*frac)
        draw.line([tx, by0, tx, by0+lw*3], fill=c, width=max(lw-2,3))


ICON_FN = {
    "bell":     _icon_bell,
    "calendar": _icon_calendar,
    "scissors": _icon_scissors,
    "syringe":  _icon_syringe,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Image (2×2 grid)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def make_rich_menu_image(save_path: str | None = None) -> bytes:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("❌ pillow ไม่ได้ติดตั้ง — รัน: pip install pillow")
        sys.exit(1)

    img  = Image.new("RGB", (W, H), "#E5E7EB")
    draw = ImageDraw.Draw(img)

    PAD  = 22   # ขอบนอก
    GAP  = 16   # ช่องว่างระหว่างปุ่ม
    RAD  = 44   # ความโค้งมน

    col_w = (W - PAD*2 - GAP)   // 2   # ความกว้างแต่ละคอลัมน์
    row_h = (H - PAD*2 - GAP)   // 2   # ความสูงแต่ละแถว

    font_title = _find_font(110)
    lw = 12   # line width icon

    for idx, (label, _, bg, border, icon_key) in enumerate(BUTTONS):
        col = idx % 2
        row = idx // 2
        x0 = PAD + col * (col_w + GAP)
        y0 = PAD + row * (row_h + GAP)
        x1 = x0 + col_w
        y1 = y0 + row_h

        # border glow (สีอ่อน)
        _panel(draw, x0, y0, x1, y1, border, RAD+4)
        # main panel
        _panel(draw, x0+8, y0+8, x1-8, y1-8, bg, RAD)

        cx = (x0+x1) // 2
        cy = (y0+y1) // 2

        ICON_Y  = cy - row_h // 5
        LABEL_Y = cy + row_h // 4

        # icon
        ICON_FN[icon_key](draw, cx, ICON_Y, size=int(row_h*0.38), c="#FFFFFF", lw=lw)

        # label
        _text(draw, cx, LABEL_Y, label, font_title, "#FFFFFF")

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    img_bytes = buf.getvalue()

    dest = save_path or str(Path(__file__).parent / "richmenu_preview.png")
    Path(dest).write_bytes(img_bytes)
    print(f"   Preview saved → {dest}  ({len(img_bytes):,} bytes)")
    return img_bytes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LINE API helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _hdr():
    return {"Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type": "application/json"}


def list_rich_menus() -> list[dict]:
    r = requests.get("https://api.line.me/v2/bot/richmenu/list",
                     headers=_hdr(), timeout=15)
    r.raise_for_status()
    return r.json().get("richmenus", [])


def create_rich_menu() -> str:
    col_w = W // 2
    row_h = H // 2
    areas = []
    for idx, (label, send_text, *_) in enumerate(BUTTONS):
        col = idx % 2
        row = idx // 2
        areas.append({
            "bounds": {
                "x": col * col_w,
                "y": row * row_h,
                "width":  col_w,
                "height": row_h,
            },
            "action": {
                "type":  "message",
                "label": label,
                "text":  send_text,
            },
        })

    body = {
        "size":        {"width": W, "height": H},
        "selected":    True,
        "name":        "DogCatLovely_MainMenu_v2",
        "chatBarText": "เมนู 🐾",
        "areas":       areas,
    }
    r = requests.post("https://api.line.me/v2/bot/richmenu",
                      headers=_hdr(), json=body, timeout=15)
    if not r.ok:
        print(f"❌ Create failed: {r.status_code} — {r.text[:300]}")
        sys.exit(1)
    return r.json()["richMenuId"]


def upload_image(menu_id: str, img_bytes: bytes):
    r = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{menu_id}/content",
        headers={"Authorization": f"Bearer {LINE_TOKEN}",
                 "Content-Type": "image/png"},
        data=img_bytes, timeout=60,
    )
    if not r.ok:
        print(f"❌ Upload failed: {r.status_code} — {r.text[:300]}")
        sys.exit(1)


def set_default(menu_id: str):
    r = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{menu_id}",
        headers=_hdr(), timeout=15,
    )
    if not r.ok:
        print(f"❌ Set default failed: {r.status_code} — {r.text[:300]}")
        sys.exit(1)


def delete_menu(menu_id: str) -> bool:
    r = requests.delete(f"https://api.line.me/v2/bot/richmenu/{menu_id}",
                        headers=_hdr(), timeout=15)
    return r.ok


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def main():
    args = set(sys.argv[1:])

    if "--preview" in args:
        print("Generating preview image...")
        make_rich_menu_image()
        print("Done — เปิด richmenu_preview.png เพื่อดูตัวอย่าง")
        return

    if "--list" in args:
        for m in list_rich_menus():
            sel = "✅" if m.get("selected") else "  "
            print(f"  {sel}  {m['richMenuId']}  {m['name']}")
        return

    if "--delete" in args:
        for m in list_rich_menus():
            ok = delete_menu(m["richMenuId"])
            print(f"  {'deleted' if ok else 'FAIL'}  {m['richMenuId']}")
        return

    if not LINE_TOKEN:
        print("❌ LINE token ไม่ได้ตั้งค่า — ตรวจสอบ .env")
        sys.exit(1)

    print("=" * 60)
    print("  Dog and Cat Lovely — Rich Menu 4 ปุ่ม")
    print("=" * 60)

    # 1. ลบ menu เก่า
    print("\nลบ menu เก่า...")
    for m in list_rich_menus():
        ok = delete_menu(m["richMenuId"])
        print(f"  {'ok' if ok else 'fail'}  {m['richMenuId']}")

    # 2. สร้าง object
    print("\nสร้าง Rich Menu object...")
    menu_id = create_rich_menu()
    print(f"  richMenuId = {menu_id}")

    # 3. สร้างรูปและ upload
    print("\nสร้าง image...")
    img = make_rich_menu_image()
    print("Upload image...")
    upload_image(menu_id, img)
    print("  image uploaded")

    # 4. Set default
    print("\nSet default...")
    set_default(menu_id)
    print("  set as default for all users")

    print()
    print("=" * 60)
    print("Rich Menu สร้างสำเร็จ!")
    print(f"  ID: {menu_id}")
    print()
    for btn in BUTTONS:
        print(f"  [{btn[0]}] → '{btn[1]}'")
    print("=" * 60)


if __name__ == "__main__":
    main()
