"""
setup_rich_menu.py — สร้าง LINE Rich Menu สำหรับ Dog & Cat Lovely
================================================================
Rich Menu 2 ปุ่ม (2500×843 px):
  ซ้าย: 📋 ลงทะเบียนรับนัด → message "ลงทะเบียน"
  ขวา:  📞 ติดต่อคลินิก    → message "ติดต่อคลินิก"

Usage:
    python setup_rich_menu.py           # สร้าง + set เป็น default
    python setup_rich_menu.py --list    # แสดงรายการ Rich Menu ที่มีอยู่
    python setup_rich_menu.py --delete  # ลบ Rich Menu ทั้งหมดของ channel
    python setup_rich_menu.py --preview # สร้างรูปอย่างเดียว (richmenu_preview.png)
"""
import io
import json
import os
import sys
from pathlib import Path

# แก้ปัญหา UnicodeEncodeError บน Windows console (cp874/cp1252)
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

# Rich Menu size — half-screen (ความสูงพอดีไม่บังแชท)
W, H = 2500, 843

CLINIC_PHONE  = "080-4288181"
CLINIC_PHONE2 = "090-1556446"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Image Generation (Pillow)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _find_thai_font(size: int):
    """ค้นหา font ที่รองรับภาษาไทย → ImageFont หรือ None"""
    from PIL import ImageFont
    candidates = [
        r"C:\Windows\Fonts\THSarabunNew Bold.ttf",
        r"C:\Windows\Fonts\THSarabunNew.ttf",
        r"C:\Windows\Fonts\NotoSansThai-Bold.ttf",
        r"C:\Windows\Fonts\NotoSansThai-Regular.ttf",
        r"C:\Windows\Fonts\AngsanaNew.ttf",
        r"C:\Windows\Fonts\cordia.ttc",     # Cordia New (มักติดตั้งใน Windows)
        r"C:\Windows\Fonts\cordia.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for fp in candidates:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_panel(draw, x0, y0, x1, y1, bg_color, radius=50):
    """วาด rounded rectangle (fallback สำหรับ Pillow < 8.2)"""
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=bg_color)
    except AttributeError:
        # Pillow < 8.2 ไม่มี rounded_rectangle
        draw.rectangle([x0, y0, x1, y1], fill=bg_color)


def _draw_centered_text(draw, cx, cy, text, font, fill, line_spacing=1.2):
    """วาด text กึ่งกลาง (anchor='mm' ต้องการ Pillow >= 8.0, fallback ด้วย bbox)"""
    try:
        draw.text((cx, cy), text, fill=fill, font=font, anchor="mm")
    except TypeError:
        # Pillow เก่า — คำนวณ position เอง
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except AttributeError:
            w, h = draw.textsize(text, font=font)
        draw.text((cx - w // 2, cy - h // 2), text, fill=fill, font=font)


def _draw_icon_bell(draw, cx, cy, size=120, color="#FFFFFF", lw=14):
    """วาดไอคอนกระดิ่งแจ้งเตือน (notification bell) — ชัดเจน"""
    r = size // 2
    bw = int(r * 1.5)   # ความกว้างกระดิ่ง
    bh = int(r * 1.1)   # ความสูงโดม
    top_y    = cy - r + 10
    bottom_y = cy + int(r * 0.35)

    # ก้านบน (stem ที่ยึดกระดิ่งกับเพดาน)
    draw.line([cx, top_y - lw * 2, cx, top_y + 2], fill=color, width=max(lw - 4, 6))

    # โดมกระดิ่ง (arc บน)
    draw.arc([cx - bw // 2, top_y, cx + bw // 2, top_y + bh * 2],
             start=180, end=360, fill=color, width=lw)

    # ด้านข้างทั้งสอง (เส้นตรง)
    side_top = top_y + bh
    draw.line([cx - bw // 2, side_top, cx - bw // 2, bottom_y], fill=color, width=lw)
    draw.line([cx + bw // 2, side_top, cx + bw // 2, bottom_y], fill=color, width=lw)

    # ขอบล่าง (rim)
    rim_ext = lw * 2
    draw.line([cx - bw // 2 - rim_ext, bottom_y,
               cx + bw // 2 + rim_ext, bottom_y], fill=color, width=lw)

    # ลูกตุ้ม (clapper)
    cr = lw + 4
    draw.ellipse([cx - cr, bottom_y + 4, cx + cr, bottom_y + 4 + cr * 2], fill=color)


def _draw_icon_chat(draw, cx, cy, size=120, color="#FFFFFF", lw=14):
    """วาดไอคอน chat bubble (สัญลักษณ์ติดต่อ/แชท) — ชัดเจน"""
    bw = int(size * 0.95)   # ความกว้าง bubble
    bh = int(size * 0.72)   # ความสูง bubble
    rad = lw * 3

    x0 = cx - bw // 2
    y0 = cy - bh // 2
    x1 = cx + bw // 2
    y1 = cy + bh // 2

    # วาด rounded rectangle (bubble body)
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=color, width=lw)
    except AttributeError:
        draw.rectangle([x0, y0, x1, y1], outline=color, width=lw)

    # หาง bubble (tail) — สามเหลี่ยมชี้ลงซ้าย
    tail_x  = cx - bw // 5
    tail_y0 = y1 - 2
    tail_y1 = y1 + size // 4
    # เติมพื้นหลังก่อน (ให้ดูเป็น solid)
    try:
        draw.rounded_rectangle([x0 + lw, y0 + lw, x1 - lw, y1 - lw], radius=rad - lw, fill=color)
    except AttributeError:
        draw.rectangle([x0 + lw, y0 + lw, x1 - lw, y1 - lw], fill=color)
    draw.polygon([(tail_x - lw * 2, tail_y0),
                  (tail_x + lw * 2, tail_y0),
                  (tail_x,          tail_y1)], fill=color)
    # วาด outline ทับอีกรอบ
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=rad, outline=color, width=lw)
    except AttributeError:
        draw.rectangle([x0, y0, x1, y1], outline=color, width=lw)

    # 3 จุดใน bubble (dots)
    dot_color = "#059669" if color == "#FFFFFF" else "#1D4ED8"
    dot_y = cy - bh // 12
    dr = lw // 2 + 3
    for dx in [-lw * 3, 0, lw * 3]:
        draw.ellipse([cx + dx - dr, dot_y - dr,
                      cx + dx + dr, dot_y + dr], fill=dot_color)


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def make_rich_menu_image(save_path: str | None = None) -> bytes:
    """สร้าง Rich Menu image 2500×843 PNG"""
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        print("❌ pillow ไม่ได้ติดตั้ง — รัน:  pip install pillow")
        sys.exit(1)

    img  = Image.new("RGB", (W, H), "#EAECEF")
    draw = ImageDraw.Draw(img)

    PAD = 28
    MID = W // 2
    GAP = 18

    LEFT_BG  = "#059669"   # emerald-600
    RIGHT_BG = "#1D4ED8"   # blue-700

    # ── Background gradient feel (วาด 2 โทนสี) ──
    # ทำ inner glow ที่มุมบนโดยวาดซ้อน
    _draw_panel(draw, PAD, PAD, MID - GAP, H - PAD, "#10B981", radius=56)  # lighter layer
    _draw_panel(draw, PAD + 14, PAD + 14, MID - GAP - 14, H - PAD - 14, LEFT_BG, radius=44)

    _draw_panel(draw, MID + GAP, PAD, W - PAD, H - PAD, "#3B82F6", radius=56)  # lighter layer
    _draw_panel(draw, MID + GAP + 14, PAD + 14, W - PAD - 14, H - PAD - 14, RIGHT_BG, radius=44)

    # ── Fonts ──
    font_title = _find_thai_font(138)
    font_sub   = _find_thai_font(72)

    # ── ตำแหน่ง center ──
    CX_L = MID // 2
    CX_R = MID + MID // 2
    CY   = H // 2

    ICON_Y  = CY - 130    # ตำแหน่ง icon (บน)
    TEXT_Y  = CY + 55     # ข้อความหลัก
    SUB_Y   = CY + 185    # ข้อความรอง

    # ── Left panel: กระดิ่งแจ้งเตือน + ข้อความ ──
    _draw_icon_bell(draw, CX_L, ICON_Y, size=130, color="#D1FAE5", lw=14)
    _draw_centered_text(draw, CX_L, TEXT_Y, "ลงทะเบียนรับนัด", font_title, "#FFFFFF")
    _draw_centered_text(draw, CX_L, SUB_Y,  "รับแจ้งเตือนผ่าน LINE", font_sub, "#A7F3D0")

    # ── Right panel: chat bubble + ข้อความ ──
    _draw_icon_chat(draw, CX_R, ICON_Y, size=130, color="#FFFFFF", lw=14)
    _draw_centered_text(draw, CX_R, TEXT_Y, "ติดต่อคลินิก", font_title, "#FFFFFF")
    _draw_centered_text(draw, CX_R, SUB_Y,  CLINIC_PHONE, font_sub, "#BFDBFE")

    # ── Save to disk (preview) ──
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    img_bytes = buf.getvalue()

    dest = save_path or str(Path(__file__).parent / "richmenu_preview.png")
    Path(dest).write_bytes(img_bytes)
    print(f"   🖼  Preview saved → {dest}  ({len(img_bytes):,} bytes)")
    return img_bytes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LINE API helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _headers():
    return {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type":  "application/json",
    }


def list_rich_menus() -> list[dict]:
    r = requests.get("https://api.line.me/v2/bot/richmenu/list",
                     headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json().get("richmenus", [])


def create_rich_menu() -> str:
    """สร้าง Rich Menu object → คืน richMenuId"""
    body = {
        "size":        {"width": W, "height": H},
        "selected":    True,
        "name":        "DogCatLovely_MainMenu_v1",
        "chatBarText": "เมนู 🐾",
        "areas": [
            {   # ซ้าย — ลงทะเบียน
                "bounds": {"x": 0, "y": 0, "width": W // 2, "height": H},
                "action": {
                    "type":        "message",
                    "label":       "ลงทะเบียนรับนัด",
                    "text":        "ลงทะเบียน",
                },
            },
            {   # ขวา — ติดต่อ
                "bounds": {"x": W // 2, "y": 0, "width": W // 2, "height": H},
                "action": {
                    "type":        "message",
                    "label":       "ติดต่อคลินิก",
                    "text":        "ติดต่อคลินิก",
                },
            },
        ],
    }
    r = requests.post(
        "https://api.line.me/v2/bot/richmenu",
        headers=_headers(), json=body, timeout=15,
    )
    if not r.ok:
        print(f"❌ Create Rich Menu failed: {r.status_code} — {r.text[:400]}")
        sys.exit(1)
    return r.json()["richMenuId"]


def upload_image(menu_id: str, img_bytes: bytes):
    """อัปโหลด PNG image เข้า Rich Menu"""
    r = requests.post(
        f"https://api-data.line.me/v2/bot/richmenu/{menu_id}/content",
        headers={
            "Authorization": f"Bearer {LINE_TOKEN}",
            "Content-Type":  "image/png",
        },
        data=img_bytes,
        timeout=60,
    )
    if not r.ok:
        print(f"❌ Upload image failed: {r.status_code} — {r.text[:400]}")
        sys.exit(1)


def set_default_menu(menu_id: str):
    """ตั้ง Rich Menu นี้เป็น default ของทุก user"""
    r = requests.post(
        f"https://api.line.me/v2/bot/user/all/richmenu/{menu_id}",
        headers=_headers(), timeout=15,
    )
    if not r.ok:
        print(f"❌ Set default failed: {r.status_code} — {r.text[:400]}")
        sys.exit(1)


def delete_menu(menu_id: str) -> bool:
    r = requests.delete(
        f"https://api.line.me/v2/bot/richmenu/{menu_id}",
        headers=_headers(), timeout=15,
    )
    return r.ok


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Main
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main():
    args = set(sys.argv[1:])

    # ── --preview: สร้างรูปอย่างเดียว ──
    if "--preview" in args:
        print("🖼  Generating preview image only...")
        make_rich_menu_image()
        print("✅ Done — เปิด richmenu_preview.png เพื่อดูตัวอย่าง")
        return

    # ── --list ──
    if "--list" in args:
        menus = list_rich_menus()
        if not menus:
            print("(ไม่มี Rich Menu ใน channel นี้)")
        for m in menus:
            sel = "✅ default" if m.get("selected") else "  "
            print(f"  {sel}  {m['richMenuId']}  name={m['name']}")
        return

    # ── --delete ──
    if "--delete" in args:
        menus = list_rich_menus()
        if not menus:
            print("(ไม่มี Rich Menu ให้ลบ)")
            return
        for m in menus:
            ok = delete_menu(m["richMenuId"])
            print(f"  {'✅ deleted' if ok else '❌ failed'}  {m['richMenuId']}")
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  Setup Flow
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not LINE_TOKEN:
        print("❌ LINE token ไม่ได้ตั้งค่า — ตรวจสอบ LOVELY_BOT_TOKEN หรือ LINE_TOKEN ใน .env")
        sys.exit(1)

    print("=" * 60)
    print("  🐾 LINE Rich Menu Setup — Dog and Cat Lovely")
    print("=" * 60)

    # 1. ลบ menu เก่า
    print("\n🗑  ตรวจสอบ Rich Menu เก่า...")
    old_menus = list_rich_menus()
    if old_menus:
        for m in old_menus:
            ok = delete_menu(m["richMenuId"])
            print(f"   {'✅' if ok else '⚠️'} removed: {m['richMenuId']} ({m['name']})")
    else:
        print("   (ไม่มี menu เก่า)")

    # 2. สร้าง Rich Menu object
    print("\n🔨 สร้าง Rich Menu object...")
    menu_id = create_rich_menu()
    print(f"   ✅ richMenuId = {menu_id}")

    # 3. สร้างและอัปโหลดรูป
    print("\n🖼  สร้าง Rich Menu image...")
    img_bytes = make_rich_menu_image()
    print("📤 อัปโหลด image...")
    upload_image(menu_id, img_bytes)
    print("   ✅ Image uploaded")

    # 4. ตั้งเป็น default
    print("\n🔗 ตั้งเป็น default Rich Menu...")
    set_default_menu(menu_id)
    print("   ✅ Set as default for all users")

    # 5. สรุป
    print()
    print("=" * 60)
    print(f"🎉 Rich Menu สร้างสำเร็จ!")
    print(f"   ID: {menu_id}")
    print()
    print("   ปุ่มซ้าย  [📋 ลงทะเบียนรับนัด]")
    print("             → ส่งข้อความ 'ลงทะเบียน'")
    print("             → เปิด flow ลงทะเบียน HN")
    print()
    print("   ปุ่มขวา   [📞 ติดต่อคลินิก]")
    print("             → ส่งข้อความ 'ติดต่อคลินิก'")
    print("             → แสดงเบอร์โทรและที่อยู่")
    print()
    print("   🖼  Preview: richmenu_preview.png")
    print("=" * 60)
    print()
    print("💡 หมายเหตุ:")
    print("   - ลูกค้าที่เปิดแชทใหม่จะเห็น Rich Menu ทันที")
    print("   - ลูกค้าเดิมที่ยังไม่เห็น → ลองปิด-เปิดแชทใหม่")
    print("   - ตรวจสอบใน LINE Developers Console → Rich Menu tab")


if __name__ == "__main__":
    main()
