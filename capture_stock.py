"""
DRX Stock API Capture
=====================
Headless browser: login DRX, navigate to stock page, click "ใกล้หมด" filter,
capture all XHR requests + responses to find the correct API.
"""
import json
import os
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass

BASE     = os.environ.get("DRX_BASE", "http://dogcatlovely.thddns.net:8080")
USERNAME = os.environ.get("DRX_USERNAME", "dogandcatlovely")
PASSWORD = os.environ.get("DRX_PASSWORD", "")

OUT_FILE = Path(__file__).parent / "stock_capture.json"

captures: list = []

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        # Capture all XHR responses with their request data
        def on_response(response):
            try:
                req = response.request
                if req.method == "POST" and "doctordogs" in req.url:
                    post_data = req.post_data or ""
                    body = ""
                    try:
                        body_bytes = response.body()
                        body = body_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        body = "<unable to read body>"
                    captures.append({
                        "url":       req.url,
                        "post_data": post_data,
                        "status":    response.status,
                        "body_size": len(body),
                        "body_preview": body[:2000],
                    })
            except Exception as e:
                print(f"  [WARN] capture error: {e}")
        page.on("response", on_response)

        # Step 1: Login
        print("[1] Opening login page...")
        page.goto(f"{BASE}/doctordogs/login", wait_until="networkidle")

        # Fill credentials
        print(f"[2] Logging in as {USERNAME}...")
        page.fill('#username', USERNAME)
        page.fill('#password', PASSWORD)
        page.press('#password', "Enter")
        page.wait_for_load_state("networkidle")
        print(f"     current URL: {page.url}")

        # Step 2: Clear captures (login page noise)
        captures.clear()
        print()
        print("[3] Navigating to stock page...")
        page.goto(f"{BASE}/doctordogs/stock?type=-1", wait_until="networkidle")
        page.wait_for_timeout(3000)
        print(f"     Captured {len(captures)} POST requests on initial load")

        # Snapshot initial captures
        initial_count = len(captures)

        # Step 3: Click filter "ใกล้หมด"
        print()
        print("[4] Clicking filter dropdown for 'ใกล้หมด'...")
        try:
            # หา dropdown ที่มี option "ใกล้หมด"
            page.select_option("select:has(option:text('ใกล้หมด'))", label="ใกล้หมด")
            page.wait_for_timeout(4000)  # รอ filter request
            print(f"     Total captures now: {len(captures)} (new: {len(captures) - initial_count})")
        except Exception as e:
            print(f"     [WARN] couldn't click filter: {e}")
            # Fallback — try by label text
            try:
                page.locator('text=ใกล้หมด').first.click()
                page.wait_for_timeout(4000)
            except Exception as e2:
                print(f"     [WARN] fallback also failed: {e2}")

        browser.close()

    # Save captures
    OUT_FILE.write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"[SAVED] {OUT_FILE} ({len(captures)} POST requests)")

    # Summary
    print()
    print("=== POST request summary ===")
    for i, c in enumerate(captures):
        post = c["post_data"][:100]
        size = c["body_size"]
        marker = " ★" if size > 1000 else ""
        print(f"  [{i}] body={size:>6}B  data={post}{marker}")

    print()
    print("=== Largest responses (>5kB) ===")
    big = sorted(captures, key=lambda x: -x["body_size"])[:5]
    for c in big:
        if c["body_size"] > 1000:
            print(f"\n  POST data: {c['post_data']}")
            print(f"  Size: {c['body_size']} bytes")
            print(f"  Preview: {c['body_preview'][:300]}")

if __name__ == "__main__":
    main()
