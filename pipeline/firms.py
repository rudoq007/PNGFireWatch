"""NASA FIRMS VIIRS alerts from NOAA-20 / NOAA-21: the fallback for when GFW's Suomi NPP feed stops.

NOAA ends Suomi NPP (S-NPP) data delivery on 2 November 2026, 13:00 UTC. Global Forest Watch's VIIRS
dataset is fed only from the S-NPP file (wri/gfw-datapump issue #213), so from that date PNG Fire Watch
takes new alerts directly from NASA FIRMS, from the satellite set in config "firms" (default NOAA-20).

Two ways to get the data:
- Public near-real-time files (no key): the last 7 days for the South-East Asia and Australia/New
  Zealand regions. Every run adds them to inputs/firms_points.csv.gz, so missed runs of up to a week
  lose nothing.
- FIRMS API (with a free MAP_KEY in env FIRMS_MAP_KEY): any period, in 5-day requests. Used to fill
  gaps longer than a week, and by calibrate.py for the archive.

Columns are converted to the GFW names used by the rest of the pipeline.
"""
import datetime as dt
import io
import os
import time

import geopandas as gpd
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INP = os.path.join(ROOT, "inputs")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

SATS = {  # key: (public folder, public file prefix, API source NRT, API source standard, label)
    "N": ("suomi-npp-viirs-c2", "SUOMI_VIIRS_C2", "VIIRS_SNPP_NRT", "VIIRS_SNPP_SP", "Suomi NPP"),
    "N20": ("noaa-20-viirs-c2", "J1_VIIRS_C2", "VIIRS_NOAA20_NRT", "VIIRS_NOAA20_SP", "NOAA-20"),
    "N21": ("noaa-21-viirs-c2", "J2_VIIRS_C2", "VIIRS_NOAA21_NRT", "VIIRS_NOAA21_SP", "NOAA-21"),
}
REGIONS = ["SouthEast_Asia", "Australia_NewZealand"]
PUBLIC = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/{folder}/csv/{prefix}_{region}_7d.csv"
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{source}/{bbox}/{days}/{date}"
STORE = os.path.join(INP, "firms_points.csv.gz")
KEY = os.environ.get("FIRMS_MAP_KEY", "").strip()
COLS = ["latitude", "longitude", "alert__date", "alert__time_utc", "confidence__cat", "satellite", "frp", "daynight"]
S = requests.Session()


def _get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            r = S.get(url, timeout=300)
            if r.status_code == 200 and r.text.startswith("latitude"):
                return pd.read_csv(io.StringIO(r.text))
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last = str(e)[:200]
        time.sleep(5 * (i + 1))
    raise RuntimeError(f"FIRMS request failed: {url.replace(KEY, '***') if KEY else url} ({last})")


def to_gfw(df, sat):
    if not len(df):
        return pd.DataFrame(columns=COLS)
    c = df["confidence"].astype(str).str.lower().map({"low": "l", "l": "l", "nominal": "n", "n": "n", "high": "h", "h": "h"}).fillna("n")
    return pd.DataFrame({"latitude": df.latitude.astype(float).round(5), "longitude": df.longitude.astype(float).round(5),
                         "alert__date": df.acq_date.astype(str).str[:10], "alert__time_utc": df.acq_time.astype(int),
                         "confidence__cat": c, "satellite": sat, "frp": df.get("frp"), "daynight": df.get("daynight")})


_COUNTRY = None


def country_geom():
    """Country outline buffered by ~1 km, so coastal alerts are kept as in GFW."""
    global _COUNTRY
    if _COUNTRY is None:
        src = next(os.path.join(INP, f) for f in ("country.geojson", "png_provinces_all.geojson") if os.path.exists(os.path.join(INP, f)))
        g = gpd.read_file(src).to_crs(3857)
        _COUNTRY = gpd.GeoSeries([g.union_all().buffer(1000)], crs=3857).to_crs(4326).iloc[0]
    return _COUNTRY


def clip(df):
    if not len(df):
        return df
    b = country_geom().bounds
    df = df[df.longitude.between(b[0], b[2]) & df.latitude.between(b[1], b[3])]
    pts = gpd.GeoSeries(gpd.points_from_xy(df.longitude, df.latitude), crs=4326)
    return df[pts.within(country_geom()).values].reset_index(drop=True)


def recent_public(sat):
    """Last 7 days for the country from the public FIRMS files (no key)."""
    folder, prefix = SATS[sat][:2]
    frames = [to_gfw(_get(PUBLIC.format(folder=folder, prefix=prefix, region=r)), sat) for r in REGIONS]
    return clip(pd.concat(frames, ignore_index=True))


def period_api(sat, d0, d1, standard=False):
    """Any period via the FIRMS API (needs FIRMS_MAP_KEY). Standard (archive) or near-real-time source."""
    if not KEY:
        raise RuntimeError("FIRMS_MAP_KEY is not set")
    src = SATS[sat][3 if standard else 2]
    b = country_geom().bounds
    bbox = ",".join(f"{v:.3f}" for v in b)
    frames, d = [], d0
    while d <= d1:
        n = min(5, (d1 - d).days + 1)  # the area API allows 1-5 days per request
        frames.append(to_gfw(_get(API.format(key=KEY, source=src, bbox=bbox, days=n, date=d)), sat))
        d += dt.timedelta(days=n)
    return clip(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame(columns=COLS)


def update_store(sat, season_start, need_from=None):
    """Add the latest FIRMS alerts to inputs/firms_points.csv.gz and return the stored season.

    need_from: first date the season needs from FIRMS. With a key, any days before the 7-day public
    window are filled from the API."""
    old = pd.read_csv(STORE, dtype={"alert__date": str}) if os.path.exists(STORE) else pd.DataFrame(columns=COLS)
    new = recent_public(sat)
    parts = [old, new]
    if KEY and need_from is not None:
        have = set(old[old.satellite == sat].alert__date) | set(new.alert__date)
        today = dt.datetime.now(dt.timezone.utc).date()
        missing = [need_from + dt.timedelta(days=i) for i in range((today - need_from).days + 1)
                   if str(need_from + dt.timedelta(days=i)) not in have]
        if missing:
            log("FIRMS: filling", len(missing), "days from the API, from", missing[0])
            parts.append(period_api(sat, missing[0], missing[-1]))
    allp = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else pd.DataFrame(columns=COLS)
    allp = allp[allp.alert__date >= str(season_start)]
    allp = allp.drop_duplicates(subset=["latitude", "longitude", "alert__date", "alert__time_utc", "satellite"]).sort_values(["alert__date", "alert__time_utc"])
    allp.to_csv(STORE, index=False, compression="gzip")
    log("FIRMS store:", len(allp), "alerts,", allp[allp.satellite == sat].alert__date.max() if len(allp) else "none")
    return allp[allp.satellite == sat].reset_index(drop=True)
