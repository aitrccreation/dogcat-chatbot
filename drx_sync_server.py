"""
DRX Sync Mini Server
====================
Lightweight Flask server (port 5050) สำหรับปุ่ม "เริ่ม Sync DRX" บน Dashboard
ไม่ต้องรัน chatbot_server.py ตัวเต็ม

Endpoints:
  GET  /            — health check
  POST /api/run/drx — trigger DRX sync (requires X-API-Key)
  GET  /api/status  — current data snapshot

Run:
  python drx_sync_server.py
  หรือ double-click start_drx_sync_server.bat
"""
import io
import os
import sys
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from flask import Flask, request, jsonify, send_file

BASE = Path(__file__).parent
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "").strip()
PORT = int(os.environ.get("DRX_SYNC_PORT", "5050"))

logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("drx_sync")

app = Flask(__name__)
_busy_lock = threading.Lock()


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    return resp


@app.route("/", methods=["GET"])
def home():
    html_file = BASE / "vethospital_system.html"
    if html_file.exists():
        return send_file(str(html_file))
    return _cors(jsonify({"app": "DRX Sync Mini Server", "port": PORT}))


@app.route("/drx_data.json", methods=["GET"])
def drx_data():
    f = BASE / "drx_data.json"
    if not f.exists():
        return _cors(jsonify({"error": "not found"})), 404
    resp = send_file(str(f), mimetype="application/json")
    resp.headers["Cache-Control"] = "no-store"
    return _cors(resp)


@app.route("/health", methods=["GET"])
def health():
    drx_file = BASE / "drx_data.json"
    info = {
        "app": "DRX Sync Mini Server",
        "port": PORT,
        "drx_data_exists": drx_file.exists(),
    }
    if drx_file.exists():
        try:
            data = json.loads(drx_file.read_text(encoding="utf-8"))
            info["fetched_at"] = data.get("_meta", {}).get("fetched_at", "")
            info["cases"]      = len(data.get("cases", []))
            info["appts"]      = len(data.get("appointments", []))
        except Exception:
            pass
    return _cors(jsonify(info))


@app.route("/api/run/drx", methods=["GET", "POST", "OPTIONS"])
def run_drx():
    if request.method == "OPTIONS":
        return _cors(jsonify({"ok": True}))

    key = request.headers.get("X-API-Key", "") or request.args.get("key", "")
    if INTERNAL_API_KEY and key != INTERNAL_API_KEY:
        return _cors(jsonify({"error": "unauthorized"})), 403

    if not _busy_lock.acquire(blocking=False):
        return _cors(jsonify({"error": "busy — กำลัง sync อยู่ รอสักครู่"})), 409

    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        log.info("DRX sync + queue build requested")
        import appointment_sync
        appointment_sync.run_drx_sync(fetch_fresh=True)
        appointment_sync.run_queue_build()
        output = captured.getvalue()
        sys.stdout = old_stdout
        log.info(f"DRX sync + queue build done ({len(output)} chars)")
        return _cors(jsonify({
            "ok": True,
            "job": "drx+queue",
            "output": output[-3000:],
            "timestamp": datetime.now().isoformat(),
        }))
    except Exception as e:
        import traceback
        sys.stdout = old_stdout
        log.exception(f"DRX sync failed: {e}")
        return _cors(jsonify({
            "error": str(e),
            "trace": traceback.format_exc()[-800:],
            "output": captured.getvalue()[-2000:],
        })), 500
    finally:
        _busy_lock.release()


@app.route("/api/status", methods=["GET"])
def status():
    drx_file = BASE / "drx_data.json"
    info = {"drx_data_exists": drx_file.exists()}
    if drx_file.exists():
        try:
            data = json.loads(drx_file.read_text(encoding="utf-8"))
            info["fetched_at"] = data.get("_meta", {}).get("fetched_at", "")
            info["cases"]         = len(data.get("cases", []))
            info["appointments"]  = len(data.get("appointments", []))
            info["stock"]         = len(data.get("stock", []))
        except Exception as e:
            info["error"] = str(e)
    return _cors(jsonify(info))


if __name__ == "__main__":
    print("=" * 55)
    print(f"  DRX Sync Mini Server — port {PORT}")
    print(f"  Working dir: {BASE}")
    print(f"  API key set: {'✅' if INTERNAL_API_KEY else '❌ (no auth!)'}")
    print("=" * 55)
    print()
    print(f"  เปิด browser: http://localhost:{PORT}")
    print(f"  ปุ่ม Sync DRX ใน dashboard จะใช้งานได้แล้ว")
    print()
    app.run(host="0.0.0.0", port=PORT, debug=False)
