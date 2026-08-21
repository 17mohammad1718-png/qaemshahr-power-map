"""server.py — local HTTP server + polling thread for the Qaemshahr power map.

Endpoints (all JSON except / and /index.html):
    GET /api/zones      zones (polygons) + active plans today/tomorrow
    GET /api/now        current per-zone state
    GET /api/history    planned+observed minute-intervals for a Jalali date
    GET /               web/index.html (the map app)

Polling: fetch_poll.do_poll() every POLL_SECONDS (default 300) in a
background thread. Run:  python server.py [port]   (default 8765)
"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import jalali
import status_model
import fetch_poll

BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(BASE, "data", "history.jsonl")
ZONES = os.path.join(BASE, "data", "zones.json")
SAVED = os.path.join(BASE, "data", "saved.json")
INDEX = os.path.join(BASE, "web", "index.html")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))
AREAS = [31, 32, 34]

WEEKDAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]

STATE_FA = {
    "planned_off": "قطع برنامه‌ریزی‌شده",
    "observed_off": "قطع گزارش‌شده",
    "on": "وصل",
    "unknown": "نامشخص",
}

_lock = threading.Lock()
_last_poll = {"ts": 0, "ok": True, "msg": "no poll yet"}


def load_zones():
    try:
        with open(ZONES, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def load_saved():
    try:
        with open(SAVED, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data if isinstance(x, str) and x.strip()]
            return []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def save_saved(feeders):
    os.makedirs(os.path.dirname(SAVED), exist_ok=True)
    with open(SAVED, "w", encoding="utf-8") as f:
        json.dump(feeders, f, ensure_ascii=False)


def _poll_loop():
    while True:
        try:
            w, e = fetch_poll.do_poll(areas=AREAS, log=False)
            with _lock:
                _last_poll.update({"ts": time.time(), "ok": e == 0,
                                   "msg": f"written={w} errors={e}"})
        except Exception as ex:
            with _lock:
                _last_poll.update({"ts": time.time(), "ok": False,
                                   "msg": str(ex)[:200]})
        time.sleep(POLL_SECONDS)


def _planned_times(plan):
    """{feeder: [['22:00','22:00'], ...]} -> simple times list per feeder."""
    out = {}
    for f, wins in plan.items():
        times = []
        for s, e in wins:
            times.append(f"{s // 60:02d}:{s % 60:02d}")
        out[f] = times
    return out


def build_now():
    hist = status_model.load_history(HISTORY)
    zones = load_zones()
    date = jalali.today_str()
    minute = status_model.minutes_now(date)
    has_data = any(r.get("date") == date for r in hist)
    result = {"ts": time.time(), "date": date, "minute": minute,
              "zones": [], "stats": {}, "unmapped": 0,
              "meta": {"last_poll": _last_poll.get("ts"),
                       "poll_ok": _last_poll.get("ok"),
                       "poll_msg": _last_poll.get("msg"),
                       "snapshots": len(hist)}}
    all_unmapped = []
    for area in AREAS:
        plan = status_model.plan_windows(hist, area, date)
        obs, unmapped = status_model.observed_intervals(hist, area, date)
        result["unmapped"] += len(unmapped)
        all_unmapped.extend(unmapped)
        area_has = has_data and bool(plan or obs)
        for z in zones:
            if z.get("area") != area:
                continue
            f = z["feeder"]
            st = status_model.state_at(plan, obs, f, minute, has_data=area_has)
            result["zones"].append({
                "feeder": f, "name": z.get("name", f), "area": area,
                "state": st, "state_fa": STATE_FA[st],
                "planned_times": _planned_times({f: plan.get(f, [])}).get(f, []),
                "observed": obs.get(f, []),
            })
    st = [z["state"] for z in result["zones"]]
    result["stats"] = {
        "planned_off": st.count("planned_off"),
        "observed_off": st.count("observed_off"),
        "on": st.count("on"),
        "unknown": st.count("unknown"),
    }
    result["unmapped_details"] = all_unmapped
    return result


def build_zones_payload():
    hist = status_model.load_history(HISTORY)
    zones = load_zones()
    active = {}
    wd = {}
    for date in (jalali.today_str(), jalali.date_offset_str(1)):
        active[date] = {}
        for area in AREAS:
            plan = status_model.plan_windows(hist, area, date)
            active[date][area] = _planned_times(plan)
        gy, gm, gd = jalali.j2g(*(int(x) for x in date.split("/")))
        import datetime as _dt
        wd[date] = WEEKDAYS_FA[_dt.date(gy, gm, gd).weekday()]
    return {"zones": zones, "active": active, "weekdays": wd,
            "today": jalali.today_str(),
            "tomorrow": jalali.date_offset_str(1)}


def build_history(date, area):
    hist = status_model.load_history(HISTORY)
    plan = status_model.plan_windows(hist, area, date)
    obs, unmapped = status_model.observed_intervals(hist, area, date)
    return {"date": date, "area": area,
            "planned": {f: [list(w) for w in wins] for f, wins in plan.items()},
            "observed": {f: [[r["s"], r["e"]] for r in recs]
                         for f, recs in obs.items()},
            "unmapped": unmapped,
            "unmapped_count": len(unmapped),
            "t_now_minute": status_model.minutes_now(date)}


def _validate_zones(zones):
    """Validate a zones array before saving (POST /api/zones)."""
    if not isinstance(zones, list) or not zones:
        raise ValueError("zones must be a non-empty array")
    for z in zones:
        if not isinstance(z, dict) or not z.get("feeder"):
            raise ValueError("each zone needs a feeder")
        poly = z.get("polygon")
        if not isinstance(poly, list) or len(poly) < 4:
            raise ValueError(f"feeder {z.get('feeder')}: polygon empty")
        for pt in poly:
            lat, lng = pt
            if not (-90 <= lat <= 90 and -180 <= lng <= 180):
                raise ValueError(f"feeder {z.get('feeder')}: bad coordinate")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep console quiet

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path
        if p in ("/", "/index.html"):
            return self._send_file(INDEX, "text/html; charset=utf-8")
        if p == "/snapshot.json":
            # the online static map boots from this file — serve it locally too
            return self._send_file(os.path.join(BASE, "web", "snapshot.json"),
                                   "application/json; charset=utf-8")
        if p == "/api/zones":
            return self._send(build_zones_payload())
        if p == "/api/now":
            return self._send(build_now())
        if p == "/api/saved":
            return self._send(load_saved())
        if p == "/api/history":
            q = parse_qs(parsed.query)
            date = (q.get("date") or [jalali.today_str()])[0]
            area = int((q.get("area") or ["31"])[0])
            return self._send(build_history(date, area))
        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/saved":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                data = json.loads(raw)
                if not isinstance(data, list):
                    raise ValueError("saved must be a list")
                cleaned = [str(x).strip() for x in data if isinstance(x, str) and str(x).strip()]
                # keep only valid feeder codes that exist
                zones = load_zones()
                valid = {z.get("feeder") for z in zones}
                cleaned = [c for c in cleaned if c in valid]
                # deduplicate preserve order
                seen = set()
                out = []
                for c in cleaned:
                    if c not in seen:
                        seen.add(c)
                        out.append(c)
                save_saved(out)
                return self._send({"ok": True, "count": len(out), "saved": out})
            except Exception as ex:
                return self._send({"ok": False, "error": str(ex)[:200]}, code=400)
        if parsed.path != "/api/zones":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            zones = json.loads(raw)
            _validate_zones(zones)
        except Exception as ex:
            return self._send({"ok": False, "error": str(ex)[:200]}, code=400)
        # backup then write
        try:
            if os.path.exists(ZONES):
                ts = time.strftime("%Y%m%d-%H%M%S")
                os.replace(ZONES, os.path.join(
                    os.path.dirname(ZONES), f"zones-backup-{ts}.json"))
            with open(ZONES, "w", encoding="utf-8") as f:
                json.dump(zones, f, ensure_ascii=False, indent=1)
        except Exception as ex:
            return self._send({"ok": False, "error": str(ex)[:200]}, code=500)
        return self._send({"ok": True, "count": len(zones), "backup": True})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[server] http://127.0.0.1:{port}  (poll every {POLL_SECONDS}s)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] stopped")


if __name__ == "__main__":
    main()
