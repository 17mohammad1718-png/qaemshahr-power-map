"""status_model.py — pure functions to compute outage state from history.

Data model
----------
A snapshot (one line of data/history.jsonl) is the raw response of the
official API for one (area, Jalali date): a list of items with
    outage_time "22:00", is_planned bool, address "402- خیابان ساری ...",
    reason_outage, registerer, outage_number, ...

State of a feeder at minute m of a Jalali day is derived from two sources:
  * planned windows:  announced item at time T -> OFF in [T, T+2h)
  * observed events:  unplanned item (serious reason) registered at T ->
                      OFF from T until it disappears from a later snapshot.

All functions are pure (no I/O except load_history) so they are testable.

Run self-test:  python status_model.py --selftest
"""
import json
import re
import sys

import jalali

MINUTES_PER_DAY = 24 * 60
PLANNED_WINDOW_MINUTES = 120      # default window length per announced slot
SERIOUS_REASONS = ("قطعي برق", "قطعي برق يك منطقه", "خاموشي کلي معابر")

FEEDER_RE = re.compile(r"^\s*(\d{3})\s*-")


def load_history(path):
    """Load all snapshot lines, sorted by ts. Returns list of dicts."""
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        return []
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def feeder_of(address):
    """'402- خیابان ...' -> '402'; None if no feeder code."""
    m = FEEDER_RE.match(address or "")
    return m.group(1) if m else None


def _time_to_min(time_str):
    hh, mm = (int(x) for x in time_str.split(":"))
    return hh * 60 + mm


def latest_snapshot_for(history, area, date):
    """Most recent snapshot for (area, date) — carries the fullest plan."""
    best = None
    for r in history:
        if r.get("area") == area and r.get("date") == date:
            best = r
    return best


def plan_windows(history, area, date):
    """{feeder: [(start_min, end_min)]} from the latest snapshot of the day.

    The official API trims completed windows during the day (verified:
    today's planned items dropped from 20 to 8 after the 20:00 slots
    passed), so using the latest snapshot is the most accurate plan.
    """
    snap = latest_snapshot_for(history, area, date)
    if not snap:
        return {}
    out = {}
    for it in snap.get("items", []):
        if not it.get("is_planned"):
            continue
        f = feeder_of(it.get("address", ""))
        if not f:
            continue
        s = _time_to_min(it.get("outage_time", "00:00"))
        e = min(s + PLANNED_WINDOW_MINUTES, MINUTES_PER_DAY)
        out.setdefault(f, []).append((s, e))
    for f in out:
        out[f].sort()
    return out


def observed_intervals(history, area, date):
    """({feeder: [(start_min, end_min_or_None)]}, unmapped_items).

    Serious unplanned items register an outage that lasts until the item
    stops appearing in snapshots (API removes resolved items).
    """
    snaps = [r for r in history
             if r.get("area") == area and r.get("date") == date]
    if not snaps:
        return {}, []
    # item key -> (first_seen_ts, last_snapshot_ts_with_item, last_seen_ts)
    state = {}
    feeder_of_item = {}
    unmapped = []
    for r in snaps:
        seen_now = set()
        for it in r.get("items", []):
            if it.get("is_planned"):
                continue
            reason = (it.get("reason_outage") or "").strip()
            if reason and reason not in SERIOUS_REASONS:
                continue  # lamp faults / notes — not a zone outage
            key = str(it.get("outage_number")) or (it.get("address") or "").strip()
            seen_now.add(key)
            f = feeder_of(it.get("address", ""))
            if key not in state:
                state[key] = {
                    "first": r["ts"], "last_snap": r["ts"], "last_seen": r["ts"],
                    "feeder": f, "addr": it.get("address", ""),
                    "reason": reason, "time": it.get("outage_time", ""),
                    "outage_number": it.get("outage_number"),
                }
            else:
                state[key]["last_snap"] = r["ts"]
                state[key]["last_seen"] = r["ts"]
        # items not seen in this snapshot but present before -> resolved
        for key, st in state.items():
            if key not in seen_now and st["last_seen"] <= st["last_snap"] \
                    and st.get("resolved") is None:
                # resolved as of this snapshot (the API dropped it)
                st["resolved"] = r["ts"]
        # bump last_snap for all (snapshot boundary advanced)
        for st in state.values():
            st["last_snap"] = r["ts"]

    day_start = jalali.jalali_to_epoch(date, "00:00")
    day_end = day_start + 24 * 3600
    feeders = {}
    for st in state.values():
        s_min = max(0, int((st["first"] - day_start) // 60))
        end_epoch = st.get("resolved")
        e_min = (int((end_epoch - day_start) // 60) if end_epoch else None)
        if e_min is not None:
            e_min = max(s_min + 1, e_min)  # at least 1 minute
            e_min = min(e_min, MINUTES_PER_DAY)
        rec = {"s": s_min, "e": e_min, "addr": st["addr"], "reason": st["reason"],
               "time": st["time"], "outage_number": st["outage_number"]}
        f = st["feeder"]
        if f:
            feeders.setdefault(f, []).append(rec)
        else:
            unmapped.append(rec)
    return feeders, unmapped


def state_at(plan, obs, feeder, minute, has_data=True):
    """State string for one feeder at minute-of-day.

    priority: observed_off > planned_off > on > unknown
    unknown only when the whole day has no data for the area.
    """
    for s, e in obs.get(feeder, []):
        if s <= minute < e if e is not None else s <= minute:
            return "observed_off"
    for s, e in plan.get(feeder, []):
        if s <= minute < e:
            return "planned_off"
    if has_data:
        return "on"
    return "unknown"


def minutes_now(date_str):
    """Current minute-of-day for the given Jalali date (0..1439)."""
    now_ts = jalali.now().timestamp()
    day_start = jalali.jalali_to_epoch(date_str, "00:00")
    m = int((now_ts - day_start) // 60)
    return max(0, min(m, MINUTES_PER_DAY - 1))


# ---------------------------------------------------------------- self-test

def _selftest():
    import tempfile
    import os
    hist = [
        {"ts": 1000.0, "area": 31, "date": "1405/05/24",
         "items": [
             {"outage_time": "22:00", "is_planned": True,
              "address": "402- خیابان ساری ...", "reason_outage": ""},
             {"outage_time": "21:54", "is_planned": False,
              "address": "افراکتی بالا محله", "reason_outage": "قطعي برق",
              "outage_number": "A1"},
         ]},
        {"ts": 1900.0, "area": 31, "date": "1405/05/24",
         "items": [
             {"outage_time": "22:00", "is_planned": True,
              "address": "402- خیابان ساری ...", "reason_outage": ""},
             # A1 resolved -> dropped by API
         ]},
    ]
    plan = plan_windows(hist, 31, "1405/05/24")
    assert plan == {"402": [(22 * 60, 24 * 60)]}, plan
    obs, unmapped = observed_intervals(hist, 31, "1405/05/24")
    assert unmapped and unmapped[0]["outage_number"] == "A1", (obs, unmapped)
    # minute 21*60+55 = 1315 -> within observed (started ~16min, ended ~31min)
    assert state_at(plan, obs, "402", 22 * 60) == "planned_off"
    assert state_at({}, obs, "402", 22 * 60 + 1, has_data=True) == "on"
    assert state_at(plan, obs, "999", 22 * 60, has_data=False) == "unknown"
    # feeder 402 planned at 22:00 => off at 22:30, on at 00:10 next day boundary
    assert state_at(plan, obs, "402", 22 * 60 + 30) == "planned_off"
    assert state_at(plan, obs, "402", 0) == "on"
    print("selftest OK: plan_windows, observed_intervals, state_at all pass")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("use --selftest")
