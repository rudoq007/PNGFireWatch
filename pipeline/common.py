"""Shared helpers for PNG Fire Watch: GFW queries, areas of interest and history aggregation."""
import datetime as dt
import json
import os
import time

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import shape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INP = os.path.join(ROOT, "inputs")
CFG = json.load(open(os.path.join(ROOT, "config.json")))
KEY = os.environ.get("GFW_API_KEY", "").strip()
OFFICIAL = "https://data-api.globalforestwatch.org/dataset/{ds}/latest/query/json"
PROXY = "https://globalnaturewatch.org/api/data/dataset/{ds}/latest/query/json/"
GEOSTORE = "https://production-api.globalforestwatch.org/v1/geostore"
VIIRS = "nasa_viirs_fire_alerts"
S = requests.Session()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def query(ds, sql, geostore=None, tries=6):
    """Run a SQL query on a GFW Data API dataset. Uses GFW_API_KEY when set."""
    params = {"sql": sql}
    if geostore:
        params.update(geostore_id=geostore, geostore_origin="rw")
    url = (OFFICIAL if KEY else PROXY).format(ds=ds)
    headers = {"x-api-key": KEY} if KEY else {}
    last = None
    for i in range(tries):
        try:
            r = S.get(url, params=params, headers=headers, timeout=900)
            j = r.json()
            if "data" in j:
                return j["data"]
            last = str(j)[:300]
        except Exception as e:  # network or JSON error
            last = str(e)[:300]
        time.sleep(6 * (i + 1))
    raise RuntimeError(f"GFW query failed for {ds}: {last}")


def create_geostore(geom):
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": geom}]}
    r = S.post(GEOSTORE, json={"geojson": fc}, timeout=300)
    r.raise_for_status()
    return r.json()["data"]["id"]


def shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February
        return d.replace(year=d.year + years, day=28)


def season_bounds(latest):
    mm, dd = map(int, CFG["season_start"].split("-"))
    start = dt.date(latest.year, mm, dd)
    if latest < start:
        start = dt.date(latest.year - 1, mm, dd)
    return start, latest


def chunks(d0, d1, days):
    """Yield (start, end) date pairs covering d0..d1 in steps of `days`."""
    a = d0
    while a <= d1:
        b = min(a + dt.timedelta(days=days - 1), d1)
        yield a, b
        a = b + dt.timedelta(days=1)


# ---------------- areas of interest ----------------
def load_admin():
    prov = gpd.read_file(os.path.join(INP, "provinces.geojson"))
    dist = gpd.read_file(os.path.join(INP, "districts.geojson"))
    return prov, dist


def fetch_protected_areas():
    """Terrestrial and coastal WDPA protected areas for the country, from the GFW copy of the WDPA.

    Geometry is used only for counting fire alerts and is not published (WDPA terms of use)."""
    iso = CFG["iso3"]
    rows = query("wdpa_protected_areas", "SELECT site_id, name, desig_eng, iucn_cat, status, status_yr, gov_type, realm, "
                 f"gfw_area__ha, gfw_geostore_id, gfw_geojson FROM results WHERE iso3 = '{iso}'")
    out = []
    for r in rows:
        if (r.get("realm") or "").lower() == "marine":
            continue
        out.append({"id": f"pa-{r['site_id']}", "uid": str(r["site_id"]), "name": r["name"], "kind": "pa",
                    "desig": r.get("desig_eng"), "iucn": r.get("iucn_cat"), "status": r.get("status"), "status_yr": r.get("status_yr"),
                    "gov": r.get("gov_type"), "area_ha": round(float(r.get("gfw_area__ha") or 0), 1),
                    "geostore": (r.get("gfw_geostore_id") or "").replace("-", ""), "geometry": shape(json.loads(r["gfw_geojson"]))})
    return gpd.GeoDataFrame(out, crs=4326) if out else gpd.GeoDataFrame(columns=["id", "geometry"], crs=4326)


def fetch_kbas():
    """Key Biodiversity Areas for the country, from the GFW copy of the World Database of KBAs.

    Geometry is used only for counting fire alerts and is not published (KBA terms of service)."""
    iso = CFG["iso3"]
    rows = query("birdlife_key_biodiversity_areas", "SELECT sitrecid, natname, intname, kbastatus, gfw_area__ha, gfw_geostore_id, "
                 f"gfw_geojson FROM results WHERE iso3 = '{iso}'")
    out = [{"id": f"kba-{r['sitrecid']}", "uid": str(r["sitrecid"]), "name": r.get("natname") or r.get("intname"), "kind": "kba",
            "status": r.get("kbastatus"), "area_ha": round(float(r.get("gfw_area__ha") or 0), 1),
            "geostore": (r.get("gfw_geostore_id") or "").replace("-", ""), "geometry": shape(json.loads(r["gfw_geojson"]))} for r in rows]
    return gpd.GeoDataFrame(out, crs=4326)


def assign_units(P, prov, dist, pas, kbas):
    """Return a long table (point index, unit id) of every area each alert point falls in."""
    pts = gpd.GeoDataFrame({"i": range(len(P))}, geometry=gpd.points_from_xy(P.longitude, P.latitude), crs=4326)
    parts = [pd.DataFrame({"i": pts.i, "unit": "national"})]
    for g, col in ((prov, "id"), (dist, "id"), (pas, "id"), (kbas, "id")):
        if len(g):
            j = gpd.sjoin(pts, g[[col, "geometry"]], predicate="within", how="inner")
            parts.append(pd.DataFrame({"i": j.i.values, "unit": j[col].values}))
    return pd.concat(parts, ignore_index=True)


def daily_by_unit(P, units):
    """{unit: {date: [all, not_low]}} from alert points and their unit assignments."""
    t = units.merge(pd.DataFrame({"i": range(len(P)), "date": P.alert__date.str[:10].values,
                                  "nl": (P.confidence__cat.values != "l").astype(int)}), on="i")
    g = t.groupby(["unit", "date"]).agg(all=("i", "size"), nl=("nl", "sum")).reset_index()
    out = {}
    for u, d, a, n in g.itertuples(index=False):
        out.setdefault(u, {})[d] = [int(a), int(n)]
    return out
