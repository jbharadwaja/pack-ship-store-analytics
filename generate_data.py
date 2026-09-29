"""
Pack & Ship Store Analytics - simulated dataset generator.

Every number this script produces is SIMULATED. None of it is real sales data
from The UPS Store, UPS, or any other business. README.md lists the assumptions
and the public figures the simulation is loosely calibrated to.

Usage:
    python generate_data.py

Writes data/*.csv and pack_ship_store.db next to this script. The random seed
is fixed, so running it again reproduces the same dataset.
"""

import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20260929
rng = np.random.default_rng(SEED)

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = ROOT / "pack_ship_store.db"

START = date(2024, 9, 1)
END = date(2026, 8, 31)

VOLUME_SCALE = 0.815           # overall traffic knob, used to calibrate annual sales
TAX_RATE = 0.08                # sales tax on taxable items (assumption)
SHIP_RATE_INCREASE = 0.059     # shipping prices step up each January
MENU_PRICE_INCREASE = 0.03     # store menu prices step up each January
COST_INFLATION = 0.02          # supply costs step up each January
BUSINESS_SHIP_DISCOUNT = 0.08  # business accounts get 8% off shipping
PRINT_PROMO = (date(2025, 6, 1), date(2025, 6, 30), 0.15)  # 15% off all printing
VOID_RATE = 0.004
REFUND_RATE = 0.0025
MISSING_EMPLOYEE_RATE = 0.005


# ---------------------------------------------------------------------------
# Store calendar
# ---------------------------------------------------------------------------
CLOSED_DAYS = {
    date(2024, 9, 2), date(2024, 11, 28), date(2024, 12, 25),
    date(2025, 1, 1), date(2025, 5, 26), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 5, 25), date(2026, 7, 4),
}
EARLY_CLOSE = {date(2024, 12, 24): 14, date(2024, 12, 31): 16,
               date(2025, 12, 24): 14, date(2025, 12, 31): 16}


def open_hours(d):
    """(open_hour, close_hour), or None when closed. Closed Sundays and holidays."""
    if d in CLOSED_DAYS or d.weekday() == 6:
        return None
    o, c = (9, 17) if d.weekday() == 5 else (8, 19)
    return o, min(c, EARLY_CLOSE.get(d, c))


def next_open_day(d):
    while d <= END and open_hours(d) is None:
        d += timedelta(days=1)
    return d if d <= END else None


def add_months(d, n):
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    last = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, last))


def all_days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


WEEKDAY_HOURLY = [0.55, 0.80, 0.95, 1.15, 1.30, 1.10, 0.90, 0.95, 1.15, 1.25, 0.85]  # 8:00-18:59
SATURDAY_HOURLY = [0.80, 1.10, 1.30, 1.25, 1.05, 0.90, 0.75, 0.55]                    # 9:00-16:59
DOW_MULT = [1.05, 0.98, 0.97, 1.00, 1.08, 0.78]                                        # Mon..Sat


def sample_time(d, hrs):
    o, c = hrs
    profile = SATURDAY_HOURLY if d.weekday() == 5 else WEEKDAY_HOURLY
    w = np.array(profile[: c - o], dtype=float)
    h = o + int(rng.choice(len(w), p=w / w.sum()))
    return datetime.combine(d, time(h, int(rng.integers(0, 60)), int(rng.integers(0, 60))))


# ---------------------------------------------------------------------------
# Services catalog (prices are 2026 menu prices; earlier years are lower).
# Mailbox rental labor includes sorting the box's mail over the rental term.
# ---------------------------------------------------------------------------
SERVICE_COLS = ["service_id", "service_name", "category", "list_price_2026", "unit_cost_2026",
                "taxable", "labor_min_per_line", "labor_min_per_unit"]
SERVICES = [
    ("SHP-GND", "UPS Ground", "Shipping", None, None, 0, 1.0, 2.5),
    ("SHP-3DS", "UPS 3 Day Select", "Shipping", None, None, 0, 1.0, 2.5),
    ("SHP-2DA", "UPS 2nd Day Air", "Shipping", None, None, 0, 1.0, 2.5),
    ("SHP-NDA", "UPS Next Day Air", "Shipping", None, None, 0, 1.0, 2.5),
    ("RET-DROP", "Prepaid return drop-off", "Returns", 0.00, 0.00, 0, 0.5, 1.0),
    ("PKG-BOX-S", "Box - small", "Packing supplies", 4.49, 1.10, 1, 0.5, 0.2),
    ("PKG-BOX-M", "Box - medium", "Packing supplies", 6.99, 1.75, 1, 0.5, 0.2),
    ("PKG-BOX-L", "Box - large", "Packing supplies", 9.99, 2.60, 1, 0.5, 0.2),
    ("PKG-BOX-XL", "Box - extra large", "Packing supplies", 14.99, 3.90, 1, 0.5, 0.3),
    ("PKG-BUBBLE", "Bubble wrap (per ft)", "Packing supplies", 0.99, 0.18, 1, 0.3, 0.05),
    ("PKG-PEANUT", "Packing peanuts (bag)", "Packing supplies", 6.49, 1.40, 1, 0.3, 0.1),
    ("PKG-TAPE", "Packing tape (roll)", "Packing supplies", 5.99, 1.20, 1, 0.2, 0.0),
    ("PKG-ENV", "Padded envelope", "Packing supplies", 3.49, 0.60, 1, 0.2, 0.0),
    ("SVC-PACK-STD", "Packing service - standard item", "Packing services", 12.99, 0.00, 0, 1.0, 9.0),
    ("SVC-PACK-FRG", "Packing service - fragile or large item", "Packing services", 24.99, 0.00, 0, 1.0, 18.0),
    ("PRT-BW", "Black & white copy (per page)", "Printing", 0.19, 0.04, 1, 3.0, 0.02),
    ("PRT-CLR", "Color copy (per page)", "Printing", 0.89, 0.16, 1, 3.0, 0.03),
    ("PRT-POSTER", "Poster print 24x36", "Printing", 39.99, 7.50, 1, 4.0, 3.0),
    ("PRT-BIZCARD", "Business cards (box of 250)", "Printing", 49.99, 9.00, 1, 6.0, 2.0),
    ("PRT-BIND", "Binding (per book)", "Printing", 5.99, 0.90, 1, 1.0, 3.0),
    ("PRT-LAM", "Lamination (per page)", "Printing", 2.99, 0.35, 1, 1.0, 1.0),
    ("PRT-SCAN", "Scanning (per page)", "Printing", 0.99, 0.02, 0, 2.0, 0.2),
    ("MBX-S6", "Mailbox rental - small, 6 months", "Mailbox", 119.00, 10.00, 0, 5.0, 55.0),
    ("MBX-S12", "Mailbox rental - small, 12 months", "Mailbox", 219.00, 18.00, 0, 5.0, 110.0),
    ("MBX-M6", "Mailbox rental - medium, 6 months", "Mailbox", 159.00, 14.00, 0, 5.0, 55.0),
    ("MBX-M12", "Mailbox rental - medium, 12 months", "Mailbox", 289.00, 26.00, 0, 5.0, 110.0),
    ("MBX-L12", "Mailbox rental - large, 12 months", "Mailbox", 389.00, 36.00, 0, 5.0, 110.0),
    ("MBX-KEY", "Mailbox key replacement", "Mailbox", 15.00, 2.00, 1, 3.0, 0.0),
    ("NOT-SIG", "Notary (per signature)", "Notary", 15.00, 0.00, 0, 4.0, 4.0),
    ("OTH-SHRED", "Shredding (per lb)", "Other services", 1.49, 0.45, 0, 1.0, 0.15),
    ("OTH-PHOTO", "Passport photos (set of 2)", "Other services", 16.99, 1.20, 0, 1.0, 5.0),
    ("OTH-FAX", "Fax (per page)", "Other services", 1.99, 0.05, 0, 1.0, 0.3),
    ("RTL-CARD", "Greeting card", "Retail", 5.99, 2.40, 1, 0.3, 0.1),
    ("RTL-ENVPK", "Envelope pack", "Retail", 3.99, 1.10, 1, 0.3, 0.1),
]
SVC = {s[0]: dict(zip(SERVICE_COLS, s)) for s in SERVICES}
FIXED_PRICE = {"NOT-SIG", "RET-DROP"}


def menu_price(sid, d):
    """Menu price on date d: the 2026 price, stepped down ~3% for each earlier year."""
    p = SVC[sid]["list_price_2026"]
    if sid in FIXED_PRICE or p < 1.0:
        return p
    for _ in range(2026 - d.year):
        p = p / (1 + MENU_PRICE_INCREASE)
        p = (round(p / 5) * 5 - 1) if p >= 50 else round(round(p, 1) - 0.01, 2)
    return round(p, 2)


def unit_cost(sid, d):
    return SVC[sid]["unit_cost_2026"] / (1 + COST_INFLATION) ** (2026 - d.year)


# ---------------------------------------------------------------------------
# Shipping prices
# UPS 2nd Day Air 2026 retail rates (U.S. 48), from UPS's published 2026 retail
# rate guide. Other service levels are scaled from this table with assumed ratios.
# ---------------------------------------------------------------------------
RATE_WEIGHTS = [1, 2, 3, 5, 10, 15, 20, 30, 50]
TWO_DAY_AIR_2026 = {
    2: [26.15, 26.60, 27.02, 28.67, 33.80, 45.48, 53.31, 70.20, 103.40],
    3: [27.37, 27.91, 28.80, 30.77, 41.72, 52.95, 62.37, 84.79, 125.92],
    4: [28.35, 28.90, 30.60, 35.98, 51.95, 66.49, 80.22, 107.96, 161.76],
    5: [32.07, 34.71, 38.35, 48.78, 71.62, 95.06, 115.93, 157.18, 221.52],
    6: [39.28, 44.27, 50.80, 64.44, 101.82, 143.29, 174.80, 239.70, 377.33],
    7: [41.49, 47.79, 55.35, 70.42, 111.95, 151.09, 188.98, 260.31, 402.08],
    8: [42.30, 50.26, 57.65, 74.61, 115.62, 157.35, 194.74, 268.94, 414.84],
}
LEVEL_IDS = ["SHP-GND", "SHP-3DS", "SHP-2DA", "SHP-NDA"]
LEVEL_NAMES = {"SHP-GND": "Ground", "SHP-3DS": "3 Day Select", "SHP-2DA": "2nd Day Air", "SHP-NDA": "Next Day Air"}
SHIP_COST_RATIO = {"SHP-GND": 0.75, "SHP-3DS": 0.73, "SHP-2DA": 0.72, "SHP-NDA": 0.71}
ZONES = [2, 3, 4, 5, 6, 7, 8]
ZONE_P = [0.14, 0.10, 0.14, 0.16, 0.12, 0.12, 0.22]


def two_day_air_2026(weight, zone):
    ys = TWO_DAY_AIR_2026[zone]
    if weight <= 50:
        return float(np.interp(weight, RATE_WEIGHTS, ys))
    slope = (ys[-1] - ys[-2]) / (RATE_WEIGHTS[-1] - RATE_WEIGHTS[-2])
    return ys[-1] + slope * (weight - 50)


def ship_price(level, weight, zone, d):
    base = two_day_air_2026(weight, zone)
    if level == "SHP-GND":
        ratio = 0.56 - 0.015 * (zone - 2) - 0.035 * math.log(weight)
    elif level == "SHP-3DS":
        ratio = 0.78
    elif level == "SHP-2DA":
        ratio = 1.0
    else:
        ratio = 1.55
    return round(base * ratio / (1 + SHIP_RATE_INCREASE) ** (2026 - d.year), 2)


def sample_package(profile):
    """Returns (level, weight_lb, zone, destination_type, package_type)."""
    docs_p = {"walkin": 0.20, "ecom": 0.05, "docs": 0.85}[profile]
    docs = rng.random() < docs_p
    if docs:
        weight = 1
        level_p = [0.15, 0.00, 0.30, 0.55] if profile == "docs" else [0.35, 0.05, 0.25, 0.35]
    else:
        median = 2.2 if profile == "ecom" else 3.0
        weight = int(min(70, max(1, math.ceil(rng.lognormal(math.log(median), 0.9)))))
        level_p = [0.90, 0.04, 0.04, 0.02] if profile == "ecom" else [0.80, 0.07, 0.08, 0.05]
    level = LEVEL_IDS[int(rng.choice(4, p=level_p))]
    zone = int(rng.choice(ZONES, p=ZONE_P))
    res_p = {"walkin": 0.62, "ecom": 0.80, "docs": 0.30}[profile]
    dest = "Residential" if rng.random() < res_p else "Commercial"
    return level, weight, zone, dest, ("Envelope" if docs else "Box")


# ---------------------------------------------------------------------------
# Line helpers
# ---------------------------------------------------------------------------
def line(sid, qty, d, disc_rate=0.0):
    up = menu_price(sid, d)
    if SVC[sid]["category"] == "Printing" and PRINT_PROMO[0] <= d <= PRINT_PROMO[1]:
        disc_rate = max(disc_rate, PRINT_PROMO[2])
    gross = round(up * qty, 2)
    disc = round(gross * disc_rate, 2)
    return {"sid": sid, "qty": int(qty), "unit_price": up, "discount": disc,
            "revenue": round(gross - disc, 2), "cost": round(unit_cost(sid, d) * qty, 2), "ship": None}


def ship_line(pkg, d, disc_rate=0.0):
    level, weight, zone, dest, ptype = pkg
    up = ship_price(level, weight, zone, d)
    disc = round(up * disc_rate, 2)
    return {"sid": level, "qty": 1, "unit_price": up, "discount": disc, "revenue": round(up - disc, 2),
            "cost": round(up * SHIP_COST_RATIO[level], 2),
            "ship": (LEVEL_NAMES[level], weight, zone, dest, ptype)}


def consolidate(lines):
    """Merge repeated non-shipping items into one line, like a register would."""
    out, idx = [], {}
    for ln in lines:
        if ln["ship"] is not None:
            out.append(ln)
            continue
        key = (ln["sid"], ln["unit_price"])
        if key in idx:
            o = out[idx[key]]
            o["qty"] += ln["qty"]
            for f in ("discount", "revenue", "cost"):
                o[f] = round(o[f] + ln[f], 2)
        else:
            idx[key] = len(out)
            out.append(dict(ln))
    return out


# ---------------------------------------------------------------------------
# Staff and schedule
# ---------------------------------------------------------------------------
EMPLOYEES = [
    # id, first_name, role, employment_type, hire_date, termination_date, hourly_wage
    ("E01", "Maria", "Store manager", "Full-time", date(2019, 4, 15), None, 26.00),
    ("E02", "Kevin", "Senior associate", "Full-time", date(2021, 8, 2), None, 21.00),
    ("E03", "Aisha", "Associate", "Full-time", date(2023, 2, 13), None, 19.50),
    ("E04", "Daniel", "Associate", "Part-time", date(2023, 10, 9), None, 18.50),
    ("E05", "Priya", "Associate", "Part-time", date(2025, 3, 3), None, 18.00),
    ("E06", "Tom", "Associate", "Part-time", date(2022, 6, 20), date(2025, 2, 21), 18.00),
    ("E07", "Sam", "Seasonal associate", "Seasonal", date(2024, 11, 12), None, 18.00),
]
# Hidden behaviour, not exported: how often each person adds packing supplies or
# the packing service, and how much of the register work they take while on shift.
UPSELL = {"E01": 1.00, "E02": 1.30, "E03": 0.95, "E04": 0.78, "E05": 0.92, "E06": 0.82, "E07": 0.65}
PACK_SVC = {"E01": 1.00, "E02": 1.40, "E03": 0.90, "E04": 0.80, "E05": 0.95, "E06": 0.80, "E07": 0.55}
REGISTER_WEIGHT = {"E01": 0.60, "E02": 1.0, "E03": 1.0, "E04": 1.0, "E05": 1.0, "E06": 1.0, "E07": 1.0}
VACATIONS = {
    "E01": [(date(2025, 7, 14), date(2025, 7, 25))],
    "E02": [(date(2025, 3, 17), date(2025, 3, 21)), (date(2026, 6, 8), date(2026, 6, 12))],
    "E03": [(date(2025, 10, 6), date(2025, 10, 10))],
}
HOLIDAY_SEASONS = [(date(2024, 11, 18), date(2024, 12, 23)), (date(2025, 11, 17), date(2025, 12, 23))]


def part_timer(d):
    if d <= date(2025, 2, 21):
        return "E06"
    if d >= date(2025, 3, 3):
        return "E05"
    return "E04"


def build_shifts(d, hrs):
    o, c = hrs
    wd = d.weekday()
    plan = []
    if wd == 5:
        plan += [("E03", o - 0.25, c + 0.25), (part_timer(d), o - 0.25, c + 0.25)]
    else:
        plan += [("E01", 7.75, 16.25), ("E02", 9.75, 18.25), ("E04" if wd == 0 else "E03", 11.0, 19.25)]
        if wd == 2:
            plan.append(("E04", 15.0, 19.25))
        if wd in (3, 4):
            plan.append((part_timer(d), 15.0, 19.25))
    if any(a <= d <= b for a, b in HOLIDAY_SEASONS):
        plan.append(("E07", 10.0, 18.5))

    working = {emp for emp, _, _ in plan}
    staffed = []
    for emp, s, e in plan:
        if any(a <= d <= b for a, b in VACATIONS.get(emp, [])):
            cover = next((x for x in ["E04", part_timer(d), "E03", "E02"] if x not in working), None)
            if cover is None:
                continue
            working.add(cover)
            emp = cover
        staffed.append((emp, s, e))

    shifts = []
    for emp, s, e in staffed:
        e = min(e, c + 0.25)
        s = min(s, e)
        if e - s >= 1:
            shifts.append((emp, s, e))
    return shifts


def pick_employee(t, shifts):
    h = t.hour + t.minute / 60
    cands = [e for e, s, en in shifts if s <= h < en] or [e for e, _, _ in shifts]
    w = np.array([REGISTER_WEIGHT[e] for e in cands])
    return cands[int(rng.choice(len(cands), p=w / w.sum()))]


# ---------------------------------------------------------------------------
# Walk-in visits
# ---------------------------------------------------------------------------
BASE = {"return": 24.0, "ship": 27.0, "print": 13.0, "notary": 4.2, "supplies": 5.0, "other": 5.0}
MONTH_MULT = {
    "ship":     {1: 0.86, 2: 0.90, 3: 0.96, 4: 0.95, 5: 0.98, 6: 0.94, 7: 0.90, 8: 0.93, 9: 0.98, 10: 1.05, 11: 1.35, 12: 1.85},
    "return":   {1: 1.55, 2: 1.05, 3: 0.98, 4: 0.97, 5: 0.98, 6: 0.96, 7: 0.95, 8: 0.96, 9: 0.97, 10: 1.00, 11: 1.08, 12: 1.20},
    "print":    {1: 0.95, 2: 1.12, 3: 1.22, 4: 1.18, 5: 1.00, 6: 0.95, 7: 0.88, 8: 1.15, 9: 1.08, 10: 1.00, 11: 0.92, 12: 0.80},
    "notary":   {1: 0.95, 2: 1.02, 3: 1.12, 4: 1.12, 5: 1.05, 6: 1.00, 7: 0.95, 8: 0.98, 9: 1.00, 10: 1.00, 11: 0.92, 12: 0.85},
    "supplies": {1: 0.90, 2: 0.95, 3: 0.97, 4: 0.97, 5: 1.00, 6: 0.97, 7: 0.95, 8: 1.02, 9: 1.00, 10: 1.03, 11: 1.20, 12: 1.60},
    "other":    {1: 1.10, 2: 1.08, 3: 1.15, 4: 1.20, 5: 1.00, 6: 1.05, 7: 0.95, 8: 0.95, 9: 0.95, 10: 0.95, 11: 0.90, 12: 0.85},
}
MAILBOX_SHARE = {"return": 0.06, "ship": 0.14, "print": 0.10, "notary": 0.08, "supplies": 0.10, "other": 0.08}


def special_mult(kind, d):
    if d.month == 12:
        if kind in ("ship", "supplies"):
            if d.day <= 20:
                return 1.12
            if d.day <= 23:
                return 0.80
            return 0.60 if d.day == 24 else 0.50
        if kind == "return":
            return 1.75 if d.day >= 26 else 0.85
    if d.month == 1 and kind == "return" and d.day <= 15:
        return 1.20
    return 1.0


def trend(kind, d):
    years = (d - START).days / 365.25
    return (1.12 if kind == "return" else 1.025) ** years


def box_for(weight):
    if weight <= 3:
        return "PKG-BOX-S"
    if weight <= 10:
        return "PKG-BOX-M"
    return "PKG-BOX-L" if weight <= 25 else "PKG-BOX-XL"


def packing_for_package(pkg, emp, supplies, services):
    weight, ptype = pkg[1], pkg[4]
    if ptype == "Envelope":
        if rng.random() < 0.22 * UPSELL[emp]:
            supplies["PKG-ENV"] += 1
        return
    if rng.random() < 0.085 * PACK_SVC[emp]:
        services["SVC-PACK-FRG" if rng.random() < 0.22 else "SVC-PACK-STD"] += 1
        supplies[box_for(weight)] += 1
        if rng.random() < 0.70:
            supplies["PKG-BUBBLE"] += int(rng.integers(4, 16))
        if rng.random() < 0.30:
            supplies["PKG-PEANUT"] += 1
        return
    if rng.random() < 0.34 * UPSELL[emp]:
        supplies[box_for(weight)] += 1
        if rng.random() < 0.45:
            supplies["PKG-BUBBLE"] += int(rng.integers(3, 16))
        if rng.random() < 0.22:
            supplies["PKG-PEANUT"] += 1
        if rng.random() < 0.18:
            supplies["PKG-TAPE"] += 1


def visit_ship(d, emp, profile="walkin", disc=0.0, n_pk=None):
    if n_pk is None:
        n_pk = int(rng.choice([1, 2, 3, 4, 5], p=[0.80, 0.14, 0.04, 0.015, 0.005]))
    lines, supplies, services = [], defaultdict(int), defaultdict(int)
    for _ in range(n_pk):
        pkg = sample_package(profile)
        lines.append(ship_line(pkg, d, disc))
        if profile != "ecom":
            packing_for_package(pkg, emp, supplies, services)
    for sid, q in list(supplies.items()) + list(services.items()):
        lines.append(line(sid, q, d))
    return lines


def visit_return(d, emp):
    n = int(rng.choice([1, 2, 3], p=[0.85, 0.12, 0.03]))
    lines = [line("RET-DROP", n, d)]
    if rng.random() < 0.08 * UPSELL[emp]:
        sid = str(rng.choice(["PKG-TAPE", "PKG-ENV", "PKG-BOX-S", "PKG-BOX-M", "RTL-CARD"],
                             p=[0.30, 0.25, 0.20, 0.15, 0.10]))
        lines.append(line(sid, 1, d))
    if rng.random() < 0.03:
        lines += visit_ship(d, emp)
    return lines


def visit_print(d, emp):
    lines = []
    for _ in range(1 + int(rng.random() < 0.25)):
        job = str(rng.choice(["bw", "clr", "poster", "cards", "bind", "lam", "scan"],
                             p=[0.40, 0.28, 0.06, 0.05, 0.06, 0.07, 0.08]))
        if job == "bw":
            lines.append(line("PRT-BW", int(min(500, max(1, rng.lognormal(math.log(22), 0.9)))), d))
        elif job == "clr":
            lines.append(line("PRT-CLR", int(min(300, max(1, rng.lognormal(math.log(10), 0.9)))), d))
        elif job == "poster":
            lines.append(line("PRT-POSTER", int(rng.choice([1, 2, 3], p=[0.75, 0.20, 0.05])), d))
        elif job == "cards":
            lines.append(line("PRT-BIZCARD", int(rng.choice([1, 2], p=[0.85, 0.15])), d))
        elif job == "bind":
            books = int(rng.integers(1, 5))
            lines.append(line("PRT-BIND", books, d))
            lines.append(line("PRT-BW", books * int(rng.integers(15, 80)), d))
        elif job == "lam":
            lines.append(line("PRT-LAM", int(rng.integers(1, 11)), d))
        else:
            lines.append(line("PRT-SCAN", int(rng.integers(1, 31)), d))
    return lines


def visit_notary(d, emp):
    lines = [line("NOT-SIG", int(rng.choice([1, 2, 3, 4], p=[0.60, 0.25, 0.10, 0.05])), d)]
    if rng.random() < 0.20:
        lines.append(line("PRT-BW", int(rng.integers(2, 11)), d))
    return lines


def visit_supplies(d, emp):
    opts = ["PKG-BOX-S", "PKG-BOX-M", "PKG-BOX-L", "PKG-TAPE", "PKG-ENV", "PKG-BUBBLE", "RTL-CARD", "RTL-ENVPK"]
    p = [0.14, 0.14, 0.06, 0.16, 0.14, 0.08, 0.16, 0.12]
    k = int(rng.choice([1, 2, 3], p=[0.6, 0.3, 0.1]))
    lines = []
    for sid in rng.choice(opts, size=k, replace=False, p=p):
        sid = str(sid)
        q = int(rng.integers(5, 20)) if sid == "PKG-BUBBLE" else int(rng.choice([1, 2, 3], p=[0.7, 0.2, 0.1]))
        lines.append(line(sid, q, d))
    return lines


def visit_other(d, emp):
    job = str(rng.choice(["shred", "photo", "fax", "scan"], p=[0.40, 0.32, 0.16, 0.12]))
    if job == "shred":
        return [line("OTH-SHRED", int(rng.integers(5, 41)), d)]
    if job == "photo":
        return [line("OTH-PHOTO", int(rng.choice([1, 2], p=[0.85, 0.15])), d)]
    if job == "fax":
        return [line("OTH-FAX", int(rng.integers(1, 11)), d)]
    return [line("PRT-SCAN", int(rng.integers(1, 31)), d)]


WALKIN_VISITS = {
    "return": visit_return,
    "ship": lambda d, emp: visit_ship(d, emp),
    "print": visit_print,
    "notary": visit_notary,
    "supplies": visit_supplies,
    "other": visit_other,
}


# ---------------------------------------------------------------------------
# Business accounts
# ---------------------------------------------------------------------------
INDUSTRIES = [  # industry, number of accounts, typical visits per week
    ("E-commerce seller", 8, 2.2),
    ("Real estate office", 7, 1.0),
    ("Law office", 5, 1.3),
    ("Medical or dental office", 5, 0.8),
    ("Contractor", 4, 0.6),
    ("Nonprofit or school", 4, 0.4),
    ("Other small business", 3, 0.7),
]


def make_businesses():
    out = []
    first, last = date(2016, 1, 1), date(2024, 8, 1)
    for industry, count, vpw in INDUSTRIES:
        for _ in range(count):
            out.append({
                "customer_id": f"B{1001 + len(out)}",
                "industry": industry,
                "open": first + timedelta(days=int(rng.integers(0, (last - first).days))),
                "close": None,
                "vpw": vpw * float(rng.uniform(0.6, 1.5)),
                "pkgs": float(rng.uniform(2.5, 7.5)),
            })
    by_id = {b["customer_id"]: b for b in out}
    by_id["B1001"].update(vpw=4.5, pkgs=9.0, close=date(2025, 5, 16))      # largest shipper leaves
    by_id["B1002"].update(open=date(2025, 10, 6), vpw=3.2, pkgs=6.5)       # new e-commerce seller
    by_id["B1009"].update(open=date(2025, 2, 10))
    by_id["B1014"].update(close=date(2026, 1, 30))
    by_id["B1022"].update(close=date(2025, 11, 14))
    by_id["B1027"].update(open=date(2025, 4, 7))
    by_id["B1031"].update(open=date(2025, 8, 18))
    return out


def business_season(industry, d):
    m = d.month
    if industry == "E-commerce seller":
        mult = {11: 1.50, 12: 1.90, 1: 0.85, 2: 0.90}.get(m, 1.0)
        return mult * (0.5 if m == 12 and d.day > 22 else 1.0)
    if industry == "Real estate office":
        return {4: 1.20, 5: 1.25, 6: 1.25, 7: 1.15, 12: 0.75, 1: 0.85}.get(m, 1.0)
    if industry == "Nonprofit or school":
        return {8: 1.40, 9: 1.30, 11: 1.20, 6: 0.70, 7: 0.60, 12: 0.80}.get(m, 1.0)
    if industry == "Law office":
        return 0.80 if m == 12 else 1.0
    return 1.0


def visit_business(b, d, emp):
    ind, disc = b["industry"], BUSINESS_SHIP_DISCOUNT
    for _ in range(5):
        lines = []
        if ind == "E-commerce seller":
            lines += visit_ship(d, emp, "ecom", disc, n_pk=1 + int(rng.poisson(b["pkgs"])))
            if rng.random() < 0.10:
                lines.append(line("PKG-BOX-M", int(rng.integers(5, 21)), d))
            if rng.random() < 0.25:
                lines.append(line("PKG-TAPE", int(rng.integers(1, 4)), d))
        elif ind == "Real estate office":
            if rng.random() < 0.70:
                lines.append(line("PRT-CLR", int(rng.integers(50, 301)), d))
            if rng.random() < 0.10:
                lines.append(line("PRT-POSTER", int(rng.integers(1, 4)), d))
            if rng.random() < 0.05:
                lines.append(line("PRT-BIZCARD", 1, d))
            if rng.random() < 0.30:
                lines.append(line("NOT-SIG", int(rng.integers(1, 4)), d))
            if rng.random() < 0.20:
                lines.append(ship_line(sample_package("docs"), d, disc))
        elif ind == "Law office":
            if rng.random() < 0.70:
                lines += visit_ship(d, emp, "docs", disc, n_pk=int(rng.integers(1, 4)))
            if rng.random() < 0.40:
                lines.append(line("NOT-SIG", int(rng.integers(1, 5)), d))
            if rng.random() < 0.45:
                lines.append(line("PRT-BW", int(rng.integers(50, 501)), d))
            if rng.random() < 0.15:
                lines.append(line("PRT-BIND", int(rng.integers(1, 4)), d))
        elif ind == "Medical or dental office":
            if rng.random() < 0.60:
                lines += visit_ship(d, emp, "docs", disc, n_pk=int(rng.integers(1, 3)))
            if rng.random() < 0.40:
                lines.append(line("PRT-BW", int(rng.integers(20, 201)), d))
            if rng.random() < 0.20:
                lines.append(line("OTH-FAX", int(rng.integers(1, 11)), d))
        elif ind == "Contractor":
            if rng.random() < 0.50:
                lines.append(line("PRT-POSTER", int(rng.integers(2, 9)), d))
            if rng.random() < 0.40:
                lines.append(line("PRT-BW", int(rng.integers(20, 101)), d))
            if rng.random() < 0.30:
                lines += visit_ship(d, emp, "walkin", disc, n_pk=int(rng.integers(1, 3)))
        elif ind == "Nonprofit or school":
            if rng.random() < 0.50:
                lines.append(line("PRT-CLR", int(rng.integers(50, 401)), d))
            if rng.random() < 0.35:
                lines.append(line("PRT-LAM", int(rng.integers(5, 41)), d))
            if rng.random() < 0.25:
                lines.append(line("PRT-BIND", int(rng.integers(2, 11)), d))
            if rng.random() < 0.40:
                lines.append(line("PRT-BW", int(rng.integers(50, 301)), d))
        else:
            if rng.random() < 0.50:
                lines += visit_ship(d, emp, "walkin", disc, n_pk=int(rng.integers(1, 4)))
            if rng.random() < 0.40:
                lines.append(line("PRT-BW", int(rng.integers(20, 201)), d))
            if rng.random() < 0.15:
                lines.append(line("PRT-BIZCARD", 1, d))
        if lines:
            return lines
    return [line("PRT-BW", int(rng.integers(20, 101)), d)]


# ---------------------------------------------------------------------------
# Mailbox holders
# ---------------------------------------------------------------------------
def mailbox_service(size, term):
    return {("Small", 6): "MBX-S6", ("Small", 12): "MBX-S12", ("Medium", 6): "MBX-M6",
            ("Medium", 12): "MBX-M12", ("Large", 12): "MBX-L12"}[(size, term)]


def simulate_mailboxes():
    holders, events = [], defaultdict(list)

    def new_holder(signup):
        size = ["Small", "Medium", "Large"][int(rng.choice(3, p=[0.58, 0.32, 0.10]))]
        term = 12 if size == "Large" else (12 if rng.random() < 0.55 else 6)
        h = {"customer_id": f"M{2001 + len(holders)}", "size": size, "term": term, "signup": signup, "close": None}
        holders.append(h)
        return h

    for _ in range(185):  # boxes already rented when the data starts
        h = new_holder(START - timedelta(days=int(rng.integers(20, 5 * 365))))
        due = h["signup"]
        while due < START:
            due = add_months(due, h["term"])
        h["first_due"] = due

    month = START.replace(day=1)
    while month <= END:  # new rentals
        for _ in range(int(rng.poisson(6.0 * {1: 1.4, 9: 1.2}.get(month.month, 1.0)))):
            day = next_open_day(month + timedelta(days=int(rng.integers(0, 28))))
            if day is None:
                continue
            h = new_holder(day)
            events[day].append((h["customer_id"], mailbox_service(h["size"], h["term"])))
            h["first_due"] = add_months(day, h["term"])
        month = add_months(month, 1)

    for h in holders:  # renewals, closures, key replacements
        due = h["first_due"]
        p_renew = 0.84 if h["term"] == 12 else (0.72 if h["size"] == "Small" else 0.76)
        while due <= END:
            if rng.random() < p_renew:
                day = next_open_day(due)
                if day is None:
                    break
                events[day].append((h["customer_id"], mailbox_service(h["size"], h["term"])))
                due = add_months(due, h["term"])
            else:
                h["close"] = due
                break
        h["active_from"] = h["signup"]
        h["active_to"] = h["close"] - timedelta(days=1) if h["close"] else END
        lo = max(h["active_from"], START)
        span = (h["active_to"] - lo).days
        if span > 0:
            for _ in range(int(rng.poisson(0.03 * span / 365.25))):
                day = next_open_day(lo + timedelta(days=int(rng.integers(0, span))))
                if day and day <= h["active_to"]:
                    events[day].append((h["customer_id"], "MBX-KEY"))
    return holders, events


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def payment_method(ctype, total, d):
    if total <= 0:
        return "No charge"
    if ctype == "Business account":
        return "On account" if rng.random() < 0.80 else "Card"
    mobile = 0.07 + 0.04 * (d - START).days / 730
    r = rng.random()
    if r < mobile:
        return "Mobile wallet"
    return "Cash" if r < mobile + 0.10 else "Card"


def simulate():
    businesses = make_businesses()
    holders, mailbox_events = simulate_mailboxes()
    visits, shift_rows, shifts_by_day = [], [], {}

    for d in all_days():
        hrs = open_hours(d)
        if hrs is None:
            continue
        shifts = build_shifts(d, hrs)
        shifts_by_day[d] = shifts
        for emp, s, e in shifts:
            shift_rows.append((emp, d, s, e))
        frac = (hrs[1] - hrs[0]) / (8 if d.weekday() == 5 else 11)
        active_mbx = [h["customer_id"] for h in holders if h["active_from"] <= d <= h["active_to"]]

        for kind, make in WALKIN_VISITS.items():
            lam = (BASE[kind] * VOLUME_SCALE * DOW_MULT[d.weekday()] * MONTH_MULT[kind][d.month]
                   * special_mult(kind, d) * trend(kind, d) * frac)
            if kind == "print" and PRINT_PROMO[0] <= d <= PRINT_PROMO[1]:
                lam *= 1.20
            for _ in range(int(rng.poisson(lam))):
                t = sample_time(d, hrs)
                emp = pick_employee(t, shifts)
                cid, ctype = None, "Walk-in"
                if active_mbx and rng.random() < MAILBOX_SHARE[kind]:
                    cid, ctype = active_mbx[int(rng.integers(len(active_mbx)))], "Mailbox holder"
                visits.append({"dt": t, "emp": emp, "cid": cid, "ctype": ctype, "lines": make(d, emp)})

        for b in businesses:
            if b["open"] <= d and (b["close"] is None or d < b["close"]):
                lam = (b["vpw"] / 5.4 * business_season(b["industry"], d)
                       * (0.35 if d.weekday() == 5 else 1.0) * VOLUME_SCALE * frac)
                for _ in range(int(rng.poisson(lam))):
                    t = sample_time(d, hrs)
                    emp = pick_employee(t, shifts)
                    visits.append({"dt": t, "emp": emp, "cid": b["customer_id"],
                                   "ctype": "Business account", "lines": visit_business(b, d, emp)})

        for cid, sid in mailbox_events.get(d, []):
            t = sample_time(d, hrs)
            emp = pick_employee(t, shifts)
            visits.append({"dt": t, "emp": emp, "cid": cid, "ctype": "Mailbox holder", "lines": [line(sid, 1, d)]})

    for v in visits:
        v["lines"] = consolidate(v["lines"])
        v["status"] = "Completed"
        v["refund_of"] = None
        v["pay"] = payment_method(v["ctype"], sum(l["revenue"] for l in v["lines"]), v["dt"].date())

    # data quirks: refunds, voids, missing cashier
    n = len(visits)
    paid = [i for i, v in enumerate(visits) if sum(l["revenue"] for l in v["lines"]) > 0]
    refunded = set(int(i) for i in rng.choice(paid, size=int(REFUND_RATE * n), replace=False))
    refunds = []
    for i in sorted(refunded):
        orig = visits[i]
        ln = [l for l in orig["lines"] if l["revenue"] > 0]
        ln = ln[int(rng.integers(len(ln)))]
        d0 = orig["dt"].date()
        day = d0 if rng.random() < 0.6 else next_open_day(d0 + timedelta(days=int(rng.integers(1, 11))))
        if day is None:
            continue
        hrs = open_hours(day)
        if day == d0:
            t = orig["dt"] + timedelta(minutes=int(rng.integers(10, 240)))
            if t.hour >= hrs[1]:
                t = datetime.combine(day, time(hrs[1] - 1, 55, int(rng.integers(0, 60))))
        else:
            t = sample_time(day, hrs)
        neg = dict(ln, qty=-ln["qty"], discount=-ln["discount"], revenue=-ln["revenue"], cost=-ln["cost"], ship=None)
        refunds.append({"dt": t, "emp": pick_employee(t, shifts_by_day[day]), "cid": orig["cid"],
                        "ctype": orig["ctype"], "lines": [neg], "status": "Refund",
                        "refund_of": orig, "pay": orig["pay"]})
    candidates = [i for i in range(n) if i not in refunded]
    for i in rng.choice(candidates, size=int(VOID_RATE * n), replace=False):
        visits[int(i)]["status"] = "Voided"
    everything = visits + refunds
    for i in rng.choice(len(everything), size=int(MISSING_EMPLOYEE_RATE * len(everything)), replace=False):
        everything[int(i)]["emp"] = None
    everything.sort(key=lambda v: v["dt"])
    return everything, businesses, holders, shift_rows


# ---------------------------------------------------------------------------
# Build tables and write outputs
# ---------------------------------------------------------------------------
def fmt_hours(x):
    return f"{int(x):02d}:{int(round((x - int(x)) * 60)):02d}"


def build_tables(everything, businesses, holders, shift_rows):
    tx, items, ships = [], [], []
    for n, v in enumerate(everything, start=1):
        v["tid"] = f"T{n:07d}"
    for v in everything:
        subtotal = round(sum(l["revenue"] + l["discount"] for l in v["lines"]), 2)
        discount = round(sum(l["discount"] for l in v["lines"]), 2)
        taxable = sum(l["revenue"] for l in v["lines"] if SVC[l["sid"]]["taxable"])
        tax = round(taxable * TAX_RATE, 2)
        tx.append({
            "transaction_id": v["tid"],
            "transaction_datetime": v["dt"].strftime("%Y-%m-%d %H:%M:%S"),
            "employee_id": v["emp"],
            "customer_id": v["cid"],
            "customer_type": v["ctype"],
            "payment_method": v["pay"],
            "status": v["status"],
            "refund_of_transaction_id": v["refund_of"]["tid"] if v["refund_of"] else None,
            "subtotal": subtotal,
            "discount_total": discount,
            "sales_tax": tax,
            "total": round(subtotal - discount + tax, 2),
        })
        for l in v["lines"]:
            line_id = f"L{len(items) + 1:08d}"
            items.append({
                "line_id": line_id,
                "transaction_id": v["tid"],
                "service_id": l["sid"],
                "quantity": l["qty"],
                "unit_price": l["unit_price"],
                "discount_amount": l["discount"],
                "line_revenue": l["revenue"],
                "line_cost": l["cost"],
            })
            if l["ship"] is not None:
                level, weight, zone, dest, ptype = l["ship"]
                ships.append({
                    "shipment_id": f"S{len(ships) + 1:07d}",
                    "line_id": line_id,
                    "service_level": level,
                    "billable_weight_lb": weight,
                    "zone": zone,
                    "destination_type": dest,
                    "package_type": ptype,
                })

    customers = []
    for b in businesses:
        closed = b["close"] is not None and b["close"] <= END
        customers.append({
            "customer_id": b["customer_id"], "customer_type": "Business account", "industry": b["industry"],
            "account_open_date": b["open"].isoformat(), "mailbox_size": None, "mailbox_term_months": None,
            "status": "Closed" if closed else "Active", "close_date": b["close"].isoformat() if closed else None,
        })
    for h in holders:
        closed = h["close"] is not None and h["close"] <= END
        customers.append({
            "customer_id": h["customer_id"], "customer_type": "Mailbox holder", "industry": None,
            "account_open_date": h["signup"].isoformat(), "mailbox_size": h["size"],
            "mailbox_term_months": h["term"], "status": "Closed" if closed else "Active",
            "close_date": h["close"].isoformat() if closed else None,
        })

    shifts = [{
        "shift_id": f"SH{n:05d}", "employee_id": emp, "shift_date": d.isoformat(),
        "start_time": fmt_hours(s), "end_time": fmt_hours(e), "hours": round(e - s, 2),
    } for n, (emp, d, s, e) in enumerate(shift_rows, start=1)]

    employees = [{
        "employee_id": e[0], "first_name": e[1], "role": e[2], "employment_type": e[3],
        "hire_date": e[4].isoformat(), "termination_date": e[5].isoformat() if e[5] else None,
        "hourly_wage": e[6],
    } for e in EMPLOYEES]

    services = [dict(zip(SERVICE_COLS, s)) for s in SERVICES]
    customers_df = pd.DataFrame(customers)
    customers_df["mailbox_term_months"] = customers_df["mailbox_term_months"].astype("Int64")

    return {
        "services": pd.DataFrame(services, columns=SERVICE_COLS),
        "employees": pd.DataFrame(employees),
        "shifts": pd.DataFrame(shifts),
        "customers": customers_df,
        "transactions": pd.DataFrame(tx),
        "transaction_items": pd.DataFrame(items),
        "shipments": pd.DataFrame(ships),
    }


SCHEMA = """
CREATE TABLE services (
    service_id TEXT PRIMARY KEY, service_name TEXT NOT NULL, category TEXT NOT NULL,
    list_price_2026 REAL, unit_cost_2026 REAL, taxable INTEGER NOT NULL,
    labor_min_per_line REAL NOT NULL, labor_min_per_unit REAL NOT NULL
);
CREATE TABLE employees (
    employee_id TEXT PRIMARY KEY, first_name TEXT NOT NULL, role TEXT NOT NULL,
    employment_type TEXT NOT NULL, hire_date TEXT NOT NULL, termination_date TEXT, hourly_wage REAL NOT NULL
);
CREATE TABLE shifts (
    shift_id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES employees(employee_id),
    shift_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, hours REAL NOT NULL
);
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY, customer_type TEXT NOT NULL, industry TEXT, account_open_date TEXT NOT NULL,
    mailbox_size TEXT, mailbox_term_months INTEGER, status TEXT NOT NULL, close_date TEXT
);
CREATE TABLE transactions (
    transaction_id TEXT PRIMARY KEY, transaction_datetime TEXT NOT NULL,
    employee_id TEXT REFERENCES employees(employee_id), customer_id TEXT REFERENCES customers(customer_id),
    customer_type TEXT NOT NULL, payment_method TEXT NOT NULL, status TEXT NOT NULL,
    refund_of_transaction_id TEXT REFERENCES transactions(transaction_id),
    subtotal REAL NOT NULL, discount_total REAL NOT NULL, sales_tax REAL NOT NULL, total REAL NOT NULL
);
CREATE TABLE transaction_items (
    line_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
    service_id TEXT NOT NULL REFERENCES services(service_id), quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL, discount_amount REAL NOT NULL, line_revenue REAL NOT NULL, line_cost REAL NOT NULL
);
CREATE TABLE shipments (
    shipment_id TEXT PRIMARY KEY, line_id TEXT NOT NULL REFERENCES transaction_items(line_id),
    service_level TEXT NOT NULL, billable_weight_lb INTEGER NOT NULL, zone INTEGER NOT NULL,
    destination_type TEXT NOT NULL, package_type TEXT NOT NULL
);
CREATE INDEX idx_items_transaction ON transaction_items(transaction_id);
CREATE INDEX idx_items_service ON transaction_items(service_id);
CREATE INDEX idx_tx_datetime ON transactions(transaction_datetime);
"""


def write_outputs(tables):
    DATA_DIR.mkdir(exist_ok=True)
    for name, df in tables.items():
        df.to_csv(DATA_DIR / f"{name}.csv", index=False)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    for name in ["services", "employees", "shifts", "customers", "transactions", "transaction_items", "shipments"]:
        df = tables[name].astype(object).where(tables[name].notna(), None)
        cols = list(df.columns)
        con.executemany(
            f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            df.itertuples(index=False, name=None),
        )
    con.commit()
    con.execute("VACUUM")
    con.close()


def main():
    everything, businesses, holders, shift_rows = simulate()
    tables = build_tables(everything, businesses, holders, shift_rows)
    write_outputs(tables)
    for name, df in tables.items():
        print(f"{name:<18} {len(df):>8,} rows")


if __name__ == "__main__":
    main()
