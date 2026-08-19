"""daily_report.py — morning Telegram report: today's power outlook for HOME feeder.

Home feeder = 402 (ترک محله، مسکن مهر), area 31 (قائم‌شهر مرکزی).

Pipeline (cron every 30 min, no_agent=True, silent outside report window):
    1. fresh poll of the official API (today + tomorrow, all areas)
    2. build a Persian emoji-line report about feeder 402 ONLY
    3. print to stdout -> cron delivers it to Telegram DM

Guards:
    - time gate: report only at/after REPORT_HOUR (07:00); earlier ticks silent
    - once-a-day marker: data/last_daily_report.txt holds the Jalali date of
      the last sent report; same day -> silent exit (catch-up pattern: if the
      laptop was off at 07:00, the first tick after power-on sends it)
    - --force: skip both guards (manual test)
"""
import datetime as dt
import os
import sys

import fetch_poll
import jalali
import status_model

HERE = os.path.dirname(os.path.abspath(__file__))
MARKER = os.path.join(HERE, "data", "last_daily_report.txt")
HOME_FEEDER = "402"
HOME_AREA = 31
REPORT_HOUR = 7          # 07:00 Iran time
WINDOW_MIN = status_model.PLANNED_WINDOW_MINUTES  # 120

WEEKDAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه",
               "جمعه", "شنبه", "یکشنبه"]
MONTHS_FA = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
             "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def fmt_min(m):
    """minute-of-day -> 'HH:MM' (English digits)."""
    m = min(m, 24 * 60 - 1)
    return f"{m // 60:02d}:{m % 60:02d}"


def day_title(date_str):
    """'1405/05/28' -> 'چهارشنبه 28 مرداد 1405'."""
    jy, jm, jd = (int(x) for x in date_str.split("/"))
    gy, gm, gd = jalali.j2g(jy, jm, jd)
    wd = WEEKDAYS_FA[dt.date(gy, gm, gd).weekday()]
    return f"{wd} {jd} {MONTHS_FA[jm - 1]} {jy}"


def planned_items_for_home(history, date_str):
    """Planned outage items of the latest snapshot touching home feeder."""
    snap = status_model.latest_snapshot_for(history, HOME_AREA, date_str)
    if not snap:
        return None  # no data at all for this (area, date)
    out = []
    for it in snap.get("items", []):
        if not it.get("is_planned"):
            continue
        if status_model.feeder_of(it.get("address", "")) != HOME_FEEDER:
            continue
        s = status_model._time_to_min(it.get("outage_time", "00:00"))
        e = min(s + WINDOW_MIN, 24 * 60)
        addr = (it.get("address") or "").strip()
        addr = addr.split("-", 1)[1].strip() if addr[:3].isdigit() else addr
        out.append({"s": s, "e": e, "addr": addr})
    out.sort(key=lambda r: r["s"])
    return out


def active_observed_for_home(history, date_str, now_min):
    """Unplanned outages on home feeder active right now."""
    obs, _unmapped = status_model.observed_intervals(history, HOME_AREA, date_str)
    active = []
    for rec in obs.get(HOME_FEEDER, []):
        if rec["s"] <= now_min and (rec["e"] is None or now_min < rec["e"]):
            active.append(rec)
    return active


def day_section(history, date_str, label, now_min=None):
    """Emoji lines for one day's plan on the home feeder."""
    lines = [f"📅 {label} — {day_title(date_str)}"]
    items = planned_items_for_home(history, date_str)
    if items is None:
        lines.append("⚪ داده‌ای از سامانه رسمی دریافت نشد")
        return lines
    if not items:
        lines.append("🟢 قطعی برنامه‌ریزی‌شده‌ای برای خونه ثبت نشده")
        return lines
    lines.append(f"🔴 {len(items)} قطعی برنامه‌ریزی‌شده برای فیدر خونه:")
    for r in items:
        mark = "⏰"
        if now_min is not None and r["s"] <= now_min < r["e"]:
            mark = "🔥"
        lines.append(f"{mark} {fmt_min(r['s'])} تا ~{fmt_min(r['e'])}"
                     + (f" — {r['addr']}" if r["addr"] else ""))
    return lines


def build_report():
    today = jalali.today_str()
    tomorrow = jalali.date_offset_str(1)

    w, e = fetch_poll.do_poll(log=False)
    history = status_model.load_history(fetch_poll.DEFAULT_HISTORY)
    now_min = status_model.minutes_now(today)

    lines = ["⚡ وضعیت برق خونه (فیدر 402 — ترک محله، مسکن مهر)", ""]
    lines += day_section(history, today, "امروز", now_min=now_min)

    active = active_observed_for_home(history, today, now_min)
    if active:
        lines.append("")
        lines.append("🚨 همین الان قطعی بی‌برنامه فعال روی فیدر خونه:")
        for rec in active:
            since = fmt_min(rec["s"])
            lines.append(f"🔌 از {since}"
                         + (f" — {rec.get('reason') or ''}" if rec.get("reason") else ""))

    lines.append("")
    lines += day_section(history, tomorrow, "فردا")

    if e and not w and not history:
        lines = ["⚡ گزارش برق خونه", "",
                 "⚠️ سامانه رسمی خاموشی پاسخ نداد و داده‌ای در دسترس نیست."]
    lines.append("")
    lines.append(f"🕐 ساعت گزارش: {dt.datetime.now(jalali._TZ).strftime('%H:%M')}"
                 " — منبع: khamooshi.maztozi.ir")
    return "\n".join(lines)


def already_sent_today():
    today = jalali.today_str()
    try:
        with open(MARKER, encoding="utf-8") as f:
            return f.read().strip() == today
    except OSError:
        return False


def mark_sent():
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as f:
        f.write(jalali.today_str())


def main():
    force = "--force" in sys.argv
    now = jalali.now()
    if not force:
        if now.hour < REPORT_HOUR:
            return  # silent: before report window
        if already_sent_today():
            return  # silent: already reported today
    print(build_report())
    mark_sent()


if __name__ == "__main__":
    main()
