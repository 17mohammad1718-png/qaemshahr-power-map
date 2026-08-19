"""fetch_poll.py — poll the official Mazandaran outage API and append snapshots.

Usage:
    python fetch_poll.py            # poll today + tomorrow for areas 31,32,34
    python fetch_poll.py --once     # force write even if unchanged (<240s)
    python fetch_poll.py --areas 31,34

Each poll appends one JSON line per (area, date) to data/history.jsonl:
    {"ts": epoch, "iso": "...", "jalali": "1405/05/24", "area": 31,
     "date": "1405/05/24", "items": [raw API items...]}

The official API (khamooshi.maztozi.ir) expects Jalali dates and the numeric
area id as `city`. No CORS needed here because this runs server-side.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import jalali

API_URL = "https://khamooshi.maztozi.ir/api/outages"
AREAS = [31, 32, 34]          # 31 = قائمشهر مرکزی، 32 = ناحیه 2، 34 = شبکه دو
DEFAULT_HISTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "data", "history.jsonl")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
SAME_LINE_SKIP_SECONDS = 240  # skip writing an identical (area,date) line if younger


def fetch_outages(date_str, area):
    """POST /api/outages for one Jalali date + area. Returns items list.

    Uses curl via subprocess: Python's urllib SSL handshake to this host
    times out on this machine (verified), curl works fine.
    """
    body = json.dumps({"fromDate": date_str, "toDate": date_str,
                       "city": area, "pgds": ""}).encode("utf-8")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tf:
        tf.write(body)
        req_path = tf.name
    try:
        proc = subprocess.run(
            ["curl", "-s", "-L", "-m", "45",
             "-H", "Content-Type: application/json",
             "-H", f"User-Agent: {UA}",
             "--data-binary", f"@{req_path}", API_URL],
            capture_output=True, timeout=60)
    finally:
        try:
            os.unlink(req_path)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(f"curl failed rc={proc.returncode}: "
                           f"{proc.stderr.decode('utf-8', 'replace')[:200]}")
    data = json.loads(proc.stdout.decode("utf-8"))
    if not data.get("success"):
        raise RuntimeError(f"API error for {date_str} area {area}: {data}")
    return data.get("outageList") or []


def load_last_lines(path, n=200):
    """Return list of parsed last JSON lines (empty if file missing)."""
    if not os.path.exists(path):
        return []
    lines = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(json.loads(line))
    except Exception:
        return []
    return lines[-n:]


def write_snapshot(path, date_str, area, items, force=False):
    """Append one snapshot line unless an identical young line exists."""
    now_ts = time.time()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not force:
        for ln in load_last_lines(path, 400):
            if (ln.get("area") == area and ln.get("date") == date_str
                    and ln.get("items") == items
                    and now_ts - ln.get("ts", 0) < SAME_LINE_SKIP_SECONDS):
                return False  # already have this exact snapshot recently
    rec = {
        "ts": now_ts,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now_ts)),
        "jalali": jalali.today_str(),
        "area": area,
        "date": date_str,
        "items": items,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def do_poll(areas=None, force=False, history_path=None, log=True):
    """Poll official API for today + tomorrow, append snapshots.

    Returns (written, errors) counts.
    """
    areas = areas or AREAS
    path = history_path or DEFAULT_HISTORY
    dates = [jalali.today_str(), jalali.date_offset_str(1)]
    written = 0
    errors = 0
    for area in areas:
        for date_str in dates:
            try:
                items = fetch_outages(date_str, area)
                if write_snapshot(path, date_str, area, items, force=force):
                    written += 1
                    if log:
                        planned = sum(1 for it in items if it.get("is_planned"))
                        print(f"[poll] {date_str} area={area} items={len(items)} "
                              f"(planned={planned}) -> snapshot written")
                elif log:
                    print(f"[poll] {date_str} area={area} unchanged, skipped")
            except Exception as e:
                errors += 1
                if log:
                    print(f"[poll] ERROR {date_str} area={area}: {e}", file=sys.stderr)
    return written, errors


if __name__ == "__main__":
    areas = AREAS
    force = "--once" in sys.argv
    for a in sys.argv:
        if a.startswith("--areas="):
            areas = [int(x) for x in a.split("=")[1].split(",")]
    w, e = do_poll(areas=areas, force=force)
    print(f"done: written={w} errors={e}")
    sys.exit(1 if e else 0)
