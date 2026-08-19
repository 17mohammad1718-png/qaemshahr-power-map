"""build_online.py — poll the official API and write web/snapshot.json for the
static GitHub Pages map (no server needed at view time).

Run:  python build_online.py     (from the project root)

Writes: web/snapshot.json — only when the DATA actually changed. The `ts`
field is excluded from the diff, so a no-change run leaves the file
byte-identical and the cron stays silent (no git commit, no Pages rebuild).

Exit codes: 0 = ok (published or no change), 1 = poll failed entirely
(previous snapshot kept so the online map never shows a gap) — the cron
turns that into an alert.
"""
import datetime
import json
import os
import time

import jalali
import status_model
import fetch_poll

BASE = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(BASE, "data", "history.jsonl")
ZONES = os.path.join(BASE, "data", "zones.json")
OUT = os.path.join(BASE, "web", "snapshot.json")
AREAS = [31, 32, 34]
AREA = 31  # the map draws the area-31 (Qaemshahr central) feeders

WEEKDAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def _weekday(date_str):
    gy, gm, gd = jalali.j2g(*(int(x) for x in date_str.split("/")))
    return WEEKDAYS_FA[datetime.date(gy, gm, gd).weekday()]


def _hist_block(hist, date):
    """Same shape as server.py /api/history, for one Jalali date."""
    plan = status_model.plan_windows(hist, AREA, date)
    obs, unmapped = status_model.observed_intervals(hist, AREA, date)
    return {
        "t_now_minute": status_model.minutes_now(date),
        "planned": {f: [[s, e] for s, e in wins] for f, wins in plan.items()},
        "observed": {
            f: [{"s": r["s"], "e": r["e"], "addr": r.get("addr", ""),
                 "reason": r.get("reason", ""), "time": r.get("time", "")}
                for r in recs]
            for f, recs in obs.items()
        },
        "unmapped_count": len(unmapped),
    }


def main():
    # 1) poll today + tomorrow for all areas (best effort)
    written, errors = fetch_poll.do_poll(areas=AREAS, log=False)
    hist = status_model.load_history(HISTORY)
    if errors and written == 0:
        # nothing new at all — keep the previous snapshot, alert the cron
        print(f"poll failed entirely (errors={errors}) — previous snapshot kept")
        return 1

    today = jalali.today_str()
    tomorrow = jalali.date_offset_str(1)

    # same shape as server.py /api/zones (active plans per date per area)
    active = {}
    weekdays = {}
    for date in (today, tomorrow):
        active[date] = {}
        weekdays[date] = _weekday(date)
        for area in AREAS:
            plan = status_model.plan_windows(hist, area, date)
            times = {}
            for f, wins in plan.items():
                times[f] = ["%02d:%02d" % (s // 60, s % 60) for s, _ in wins]
            active[date][area] = times

    snap = {
        "ts": time.time(),
        "today": today,
        "tomorrow": tomorrow,
        "weekdays": weekdays,
        "zones": json.load(open(ZONES, encoding="utf-8")),
        "active": active,
        "history": {"today": _hist_block(hist, today),
                    "tomorrow": _hist_block(hist, tomorrow)},
        "meta": {
            "poll_ok": errors == 0,
            "errors": errors,
            "written": written,
            "snapshots": len(hist),
            "built_by": "build_online.py",
        },
    }

    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)

    # 2) publish only when data changed (ignore ts and meta counters)
    changed = True
    try:
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
        old.pop("ts", None); old.pop("meta", None)
        new = dict(snap)
        new.pop("ts", None); new.pop("meta", None)
        changed = old != new
    except (OSError, ValueError):
        changed = True

    if changed:
        os.replace(tmp, OUT)
        print(f"snapshot written: {os.path.getsize(OUT)} bytes "
              f"(poll written={written} errors={errors})")
    else:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        print("no data change — snapshot kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
