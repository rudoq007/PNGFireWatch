"""Compare NOAA-20 with Suomi NPP fire alert counts for Papua New Guinea, to calibrate the switch.

Both satellites flew together from 2018 to 2 November 2026. For every season in that overlap this
script counts alerts for the same calendar window (default 1 July to 31 October) from each satellite,
using the NASA FIRMS archive (standard processing), and reports the NOAA-20 / Suomi NPP ratio. The
median ratio is the suggested `firms.factor` in config.json: NOAA-20 counts are divided by it so the
current season can be compared with 2012-2025 on a Suomi NPP basis.

Needs a free FIRMS MAP_KEY (https://firms.modaps.eosdis.nasa.gov/api/map_key/) in env FIRMS_MAP_KEY.

    FIRMS_MAP_KEY=... python pipeline/calibrate.py [--first 2018] [--last 2025] [--window 07-01:10-31]

Writes inputs/calibration.json. About 2 x 25 requests per season (5 days each); well within the FIRMS limit of 5,000 per 10 minutes.
"""
import argparse
import datetime as dt
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import firms  # noqa: E402
from firms import INP, log  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", type=int, default=2018)
    ap.add_argument("--last", type=int, default=2025)
    ap.add_argument("--window", default="07-01:10-31")
    a = ap.parse_args()
    w0, w1 = a.window.split(":")
    rows = []
    for y in range(a.first, a.last + 1):
        d0, d1 = dt.date.fromisoformat(f"{y}-{w0}"), dt.date.fromisoformat(f"{y}-{w1}")
        rec = {"year": y, "from": str(d0), "to": str(d1)}
        for sat in ("N", "N20"):
            # standard (archive) products lag by a few months; use near-real-time for the current year
            df = firms.period_api(sat, d0, d1, standard=y < dt.date.today().year)
            rec[sat] = int(len(df))
            rec[sat + "_excl_low"] = int((df.confidence__cat != "l").sum())
            rec[sat + "_days"] = int(df.alert__date.nunique())
        rec["ratio"] = round(rec["N20"] / rec["N"], 3) if rec["N"] else None
        rec["ratio_excl_low"] = round(rec["N20_excl_low"] / rec["N_excl_low"], 3) if rec["N_excl_low"] else None
        log(y, rec)
        rows.append(rec)
    r = [x["ratio"] for x in rows if x["ratio"]]
    out = {"window": a.window, "seasons": rows, "median_ratio": round(float(np.median(r)), 3) if r else None,
           "min_ratio": min(r) if r else None, "max_ratio": max(r) if r else None,
           "note": "NOAA-20 / Suomi NPP alert counts for Papua New Guinea, FIRMS standard (SP) products. "
                   "Set config firms.factor to median_ratio to express NOAA-20 counts as Suomi NPP equivalents.",
           "made": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    json.dump(out, open(os.path.join(INP, "calibration.json"), "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("median_ratio", "min_ratio", "max_ratio")}))


if __name__ == "__main__":
    main()
