import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from collections import defaultdict

with open("drx_data.json", encoding="utf-8") as f:
    d = json.load(f)

appts = d.get("appointments", [])
groups = defaultdict(list)
for a in appts:
    mid = a.get("_raw", {}).get("appointmentModeId")
    groups[mid].append(a)

print("=== appointmentModeId mapping ===")
for mid, items in sorted(groups.items(), key=lambda x: x[0] or 0):
    ex = items[0]
    raw = ex.get("_raw", {})
    print(f"  modeId={mid}  count={len(items):3d}  color={raw.get('color','?'):<15}  date={ex['date']}  pet={ex['pet']}")

print()
print("=== รายการ modeId != 1 (อาจเป็น มาตามนัด/อื่นๆ) ===")
for a in appts:
    mid = a.get("_raw", {}).get("appointmentModeId")
    if mid != 1:
        raw = a.get("_raw", {})
        print(f"  modeId={mid}  date={a['date']}  pet={a['pet']}  service={a['service']}  color={raw.get('color')}")
