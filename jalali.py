"""Jalali (Shamsi) date helpers — thin wrapper over `jdatetime`.

Iran uses fixed UTC+3:30 (no DST since Sep 2022), so we use a fixed
offset timezone instead of zoneinfo (which needs the tzdata package on
Windows). `jdatetime` is pure-Python and battle-tested; the conversion
matches the official Iranian calendar.
"""
import datetime as _dt

import jdatetime

_TZ = _dt.timezone(_dt.timedelta(hours=3, minutes=30))


def now():
    """Current datetime in Iran time (UTC+3:30 fixed)."""
    return _dt.datetime.now(_TZ)


def g2j(gy, gm, gd):
    d = jdatetime.date.fromgregorian(date=_dt.date(gy, gm, gd))
    return (d.year, d.month, d.day)


def j2g(jy, jm, jd):
    d = jdatetime.date(jy, jm, jd).togregorian()
    return (d.year, d.month, d.day)


def today_str():
    """Today's Jalali date as 'YYYY/MM/DD'."""
    d = now()
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def date_offset_str(days):
    """Jalali date string offset by N days from today."""
    d = now() + _dt.timedelta(days=days)
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def jalali_to_epoch(date_str, time_str="00:00"):
    """'1405/05/24' + '22:00' -> epoch (Iran fixed offset)."""
    jy, jm, jd = (int(x) for x in date_str.split("/"))
    gy, gm, gd = j2g(jy, jm, jd)
    hh, mm = (int(x) for x in time_str.split(":"))
    return _dt.datetime(gy, gm, gd, hh, mm, tzinfo=_TZ).timestamp()


def epoch_to_jalali(epoch):
    """epoch -> (date_str 'YYYY/MM/DD', time_str 'HH:MM')."""
    d = _dt.datetime.fromtimestamp(epoch, tz=_TZ)
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{jy:04d}/{jm:02d}/{jd:02d}", d.strftime("%H:%M")
