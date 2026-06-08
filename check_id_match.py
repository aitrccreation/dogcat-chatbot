import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("drx_data.json", encoding="utf-8") as f:
    d = json.load(f)

cal   = d["_raw"]["calendar"]
grid  = d["_raw"]["appointments"]

cal_ids = {item.get("id") for item in cal}
grid_ids = {r["id"] for r in grid}

print(f"Calendar events: {len(cal_ids)}  Grid rows: {len(grid_ids)}")
print(f"id=261 (มาตามนัด) in calendar? {261 in cal_ids}")
print(f"id=259 (ยกเลิก)   in calendar? {259 in cal_ids}")

# Build grid status lookup
grid_status = {}
for r in grid:
    cells = r.get("cell", [])
    status_text = cells[16] if len(cells) > 16 else ""
    status_code = cells[14] if len(cells) > 14 else "1"
    if status_text:
        grid_status[r["id"]] = status_text

print(f"\nGrid rows with status: {grid_status}")

# Check appointments array (processed) - do they have _raw.id?
appts = d["appointments"]
matched = 0
for a in appts:
    rid = a.get("_raw", {}).get("id")
    if rid and rid in grid_status:
        matched += 1
        print(f"  MATCH: appt id={rid}  pet={a['pet']}  status={grid_status[rid]}")

print(f"\nMatched: {matched}")
