"""Build or extend inputs/history.json: daily VIIRS alert counts per area since the baseline year.

History is the comparison baseline. It covers every day before the current fire season and only
needs extending once a year, when a new season starts; update.py calls extend() automatically.

    python pipeline/history.py            # build from scratch, or extend to the current season
"""
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from common import CFG, INP, VIIRS, assign_units, chunks, daily_by_unit, fetch_kbas, fetch_protected_areas, load_admin, log, query, season_bounds  # noqa: E402

HIST = os.path.join(INP, "history.json")


def fetch_points(d0, d1, days=20):
    iso = CFG["iso3"]

    def job(ab):
        a, b = ab
        rows = query(VIIRS, "SELECT latitude, longitude, alert__date, confidence__cat FROM results "
                     f"WHERE iso = '{iso}' AND alert__date >= '{a}' AND alert__date <= '{b}'")
        return pd.DataFrame(rows)
    with ThreadPoolExecutor(5) as ex:
        frames = list(ex.map(job, list(chunks(d0, d1, days))))
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["latitude", "longitude", "alert__date", "confidence__cat"])


def extend(areas=None, latest=None):
    """Make sure history covers every day before the current season. Returns (history, changed)."""
    if latest is None:
        latest = dt.date.fromisoformat(query(VIIRS, f"SELECT MAX(alert__date) AS d FROM results WHERE iso = '{CFG['iso3']}'")[0]["d"][:10])
    start, _ = season_bounds(latest)
    need_end = start - dt.timedelta(days=1)
    H = json.load(open(HIST)) if os.path.exists(HIST) else {"first": f"{CFG['baseline_first_year']}-01-01", "end": None, "units": {}}
    have_end = dt.date.fromisoformat(H["end"]) if H["end"] else dt.date.fromisoformat(H["first"]) - dt.timedelta(days=1)
    if have_end >= need_end:
        return H, False
    d0 = have_end + dt.timedelta(days=1)
    log("history: fetching alerts", d0, "to", need_end)
    prov, dist, pas, kbas = areas if areas else (*load_admin(), fetch_protected_areas(), fetch_kbas())
    # one year at a time keeps memory small
    y = d0
    while y <= need_end:
        y1 = min(dt.date(y.year, 12, 31), need_end)
        P = fetch_points(y, y1)
        log("  ", y, "to", y1, len(P), "alerts")
        if len(P):
            daily = daily_by_unit(P, assign_units(P, prov, dist, pas, kbas))
            for u, dd in daily.items():
                H["units"].setdefault(u, {}).update(dd)
        y = y1 + dt.timedelta(days=1)
    H["end"] = str(need_end)
    H["note"] = ("Daily NASA VIIRS (S-NPP) alert counts per area as [all alerts, excluding low confidence]. "
                 "Areas: national, OCHA COD provinces and districts (pcode), WDPA protected areas (pa-<site_id>), KBAs (kba-<sitrecid>).")
    json.dump(H, open(HIST, "w"), separators=(",", ":"))
    log("history saved to", H["end"], "units", len(H["units"]))
    return H, True


if __name__ == "__main__":
    extend()
