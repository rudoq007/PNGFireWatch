"""Daily data update for PNG Fire Watch.

Fetches this fire season's VIIRS alerts for the whole country from the GFW Data API, counts them
for every province, district, protected area and Key Biodiversity Area, compares each with the
same calendar period in every year since the baseline year, and writes the website data files.

    python pipeline/update.py --out _site [--force]

If the live site (env SITE_URL or config "site_url") already has the latest alert date, the script
writes changed=false to $GITHUB_OUTPUT and stops, unless --force is given.
"""
import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import mapping

sys.path.insert(0, os.path.dirname(__file__))
from common import (CFG, INP, VIIRS, assign_units, chunks, create_geostore, daily_by_unit, fetch_kbas,  # noqa: E402
                    fetch_protected_areas, load_admin, log, query, season_bounds, shift_year)
import history  # noqa: E402

# bit flags stored with every alert point (see Method tab)
FLAGS = [("tcd30", "Tree cover over 30% (2000)", "umd_tree_cover_density_2000__threshold"),
         ("primary", "Primary forest", "is__umd_regional_primary_forest_2001"),
         ("ifl", "Intact forest landscape", "is__ifl_intact_forest_landscapes_2016"),
         ("kba", "Key Biodiversity Area", "is__birdlife_key_biodiversity_areas"),
         ("pa", "Protected area (WDPA)", "wdpa_protected_areas__iucn_cat"),
         ("peat", "Peatland", "is__gfw_peatlands"),
         ("mangrove", "Mangrove (2020)", "is__gmw_global_mangrove_extent_2020"),
         ("managed", "Managed forest concession", "is__gfw_managed_forests"),
         ("mining", "Mining concession", "is__gfw_mining_concessions"),
         ("indigenous", "Indigenous and community land (LandMark)", "is__landmark_indigenous_and_community_lands")]
LOWBIT = 1 << len(FLAGS)


def gh_output(k, v):
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a") as f:
            f.write(f"{k}={v}\n")


def month_keys(start, end):
    keys, d = [], dt.date(start.year, start.month, 1)
    while d <= end:
        keys.append(d.strftime("%Y-%m"))
        d = dt.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return keys


def safe_ratio(a, b):
    return round(a / b, 2) if b else None


def season_points(start, end):
    cols = "latitude, longitude, alert__date, alert__time_utc, confidence__cat, " + ", ".join(f[2] for f in FLAGS)

    def job(ab):
        a, b = ab
        return pd.DataFrame(query(VIIRS, f"SELECT {cols} FROM results WHERE iso = '{CFG['iso3']}' "
                                         f"AND alert__date >= '{a}' AND alert__date <= '{b}'"))
    with ThreadPoolExecutor(5) as ex:
        frames = [f for f in ex.map(job, list(chunks(start, end, 7))) if len(f)]
    P = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols.replace(" ", "").split(","))
    return P.drop_duplicates(subset=["latitude", "longitude", "alert__date", "alert__time_utc"]).reset_index(drop=True)


def point_flags(P):
    f = np.zeros(len(P), dtype=int)
    for i, (_, _, col) in enumerate(FLAGS):
        v = P[col]
        if col == "umd_tree_cover_density_2000__threshold":
            on = pd.to_numeric(v, errors="coerce").fillna(0) >= 30
        elif col == "wdpa_protected_areas__iucn_cat":
            on = v.notna() & (v.astype(str) != "") & (v.astype(str).str.lower() != "none")
        else:
            on = v.fillna(False).astype(bool)
        f |= on.values.astype(int) << i
    f |= (P.confidence__cat.values == "l").astype(int) * LOWBIT
    return f


def ring_counts(units_gdf, P, km):
    """Alerts within `km` outside each area's boundary, buffered in the area's own UTM zone."""
    pts = gpd.GeoDataFrame({"nl": (P.confidence__cat.values != "l").astype(int)}, geometry=gpd.points_from_xy(P.longitude, P.latitude), crs=4326)
    out = {}
    for r in units_gdf.itertuples():
        g = gpd.GeoSeries([r.geometry], crs=4326)
        utm = g.estimate_utm_crs()
        gu = g.to_crs(utm).iloc[0]
        ring = gpd.GeoSeries([gu.buffer(km * 1000).difference(gu)], crs=utm).to_crs(4326).iloc[0]
        cand = pts.iloc[list(pts.sindex.query(ring, predicate="intersects"))]
        inside = cand[cand.within(ring)]
        out[r.id] = [int(len(inside)), int(inside.nl.sum())]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_site"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out = a.out
    for d in ("data", "downloads"):
        os.makedirs(os.path.join(out, d), exist_ok=True)

    latest = dt.date.fromisoformat(query(VIIRS, f"SELECT MAX(alert__date) AS d FROM results WHERE iso = '{CFG['iso3']}'")[0]["d"][:10])
    start, end = season_bounds(latest)
    log("latest alert", latest, "season", start, "to", end)
    site_url = os.environ.get("SITE_URL") or CFG.get("site_url") or ""
    if site_url and not a.force:
        try:
            live = requests.get(site_url.rstrip("/") + "/data/stats.json", timeout=60).json()
            if live.get("season", {}).get("end") == str(end):
                log("live site already has data to", end)
                gh_output("changed", "false")
                return
        except Exception as e:
            log("could not read live site, building anyway:", e)
    gh_output("changed", "true")

    prov, dist = load_admin()
    pas, kbas = fetch_protected_areas(), fetch_kbas()
    log("areas:", len(prov), "provinces,", len(dist), "districts,", len(pas), "protected areas,", len(kbas), "KBAs")
    H, hist_changed = history.extend((prov, dist, pas, kbas), latest)
    gh_output("history_changed", str(hist_changed).lower())

    # admin geostores (created once, kept in inputs/geostores.json)
    gpath = os.path.join(INP, "geostores.json")
    GS = json.load(open(gpath)) if os.path.exists(gpath) else {}
    new = False
    for g in (prov, dist):
        for r in g.itertuples():
            if r.id not in GS:
                try:
                    GS[r.id] = create_geostore(mapping(r.geometry.simplify(0.002)))
                    new = True
                except Exception as e:
                    log("geostore failed", r.id, e)
    if new:
        json.dump(GS, open(gpath, "w"), indent=0)
    gh_output("geostores_changed", str(new).lower())

    P = season_points(start, end)
    P["fl"] = point_flags(P)
    log("season alerts", len(P))
    U = assign_units(P, prov, dist, pas, kbas)
    cur = daily_by_unit(P, U)

    first = CFG["baseline_first_year"]
    months = month_keys(start, end)
    span = end.year - start.year
    cur_key = str(end.year)

    def years_for(uid):
        h = H["units"].get(uid, {})
        s = pd.DataFrame([(k, v[0], v[1]) for k, v in h.items()], columns=["d", "a", "n"])
        s["d"] = pd.to_datetime(s.d) if len(s) else s.d
        yrs = {}
        for y in range(first, start.year):
            k = y - start.year
            w0, w1 = pd.Timestamp(shift_year(start, k)), pd.Timestamp(shift_year(end, k))
            sel = s[(s.d >= w0) & (s.d <= w1)] if len(s) else s
            yrs[str(y + span)] = [int(sel.a.sum()) if len(sel) else 0, int(sel.n.sum()) if len(sel) else 0]
        c = cur.get(uid, {})
        yrs[cur_key] = [sum(v[0] for v in c.values()), sum(v[1] for v in c.values())]
        return yrs

    def monthly_for(uid):
        c = cur.get(uid, {})
        return {m: [sum(v[0] for d, v in c.items() if d.startswith(m)), sum(v[1] for d, v in c.items() if d.startswith(m))] for m in months}

    def summarise(uid):
        yrs = years_for(uid)
        hist = [v for k, v in yrs.items() if k != cur_key]
        now = yrs[cur_key]
        mean = [float(np.mean([h[i] for h in hist])) if hist else 0.0 for i in (0, 1)]
        return {"years": yrs, "monthly": monthly_for(uid), "now": now, "mean": [round(m, 1) for m in mean],
                "ratio": [safe_ratio(now[i], mean[i]) for i in (0, 1)],
                "rank": [1 + sum(h[i] > now[i] for h in hist) for i in (0, 1)],
                "y2015": yrs.get("2015", [0, 0])}

    def flag_counts(idx):
        f = P["fl"].values[idx] if len(idx) else np.array([], dtype=int)
        nl = (f & LOWBIT) == 0
        return {k: [int(((f >> i) & 1).sum()), int((((f >> i) & 1) & nl).sum())] for i, (k, _, _) in enumerate(FLAGS)}

    members = U.groupby("unit").i.apply(np.array).to_dict()
    national = {"id": "national", "name": CFG.get("country_name", "Papua New Guinea"), **summarise("national"),
                "flags": flag_counts(np.arange(len(P)))}
    prov_rows = []
    for r in prov.itertuples():
        prov_rows.append({"id": r.id, "name": r.name, "geostore": GS.get(r.id), **summarise(r.id), "flags": flag_counts(members.get(r.id, []))})
    dist = dist.copy()
    dist["area_km2"] = (dist.to_crs(6933).area / 1e6).round(1)
    dist_rows = []
    for r in dist.itertuples():
        s = summarise(r.id)
        dist_rows.append({"id": r.id, "name": r.name, "province_id": r.province_id, "geostore": GS.get(r.id), "area_km2": r.area_km2,
                          "density": [round(100 * s["now"][i] / r.area_km2, 2) if r.area_km2 else 0 for i in (0, 1)], **s,
                          "flags": flag_counts(members.get(r.id, []))})
    rk = ring_counts(pd.concat([pas[["id", "geometry"]], kbas[["id", "geometry"]]]), P, CFG["ring_km"])
    # which province each PA / KBA mostly lies in
    def main_prov(g):
        if not len(g):
            return {}
        gi = gpd.overlay(g[["id", "geometry"]].to_crs(6933), prov[["id", "geometry"]].rename(columns={"id": "pid"}).to_crs(6933), how="intersection", keep_geom_type=True)
        gi["a"] = gi.area
        top = gi.sort_values("a", ascending=False).drop_duplicates("id")
        return dict(zip(top.id, top.pid))
    pa_prov, kba_prov = main_prov(pas), main_prov(kbas)
    area_rows = []
    for g, pmap in ((pas, pa_prov), (kbas, kba_prov)):
        for r in g.itertuples():
            rec = {k: getattr(r, k) for k in g.columns if k not in ("geometry",)}
            rec.update(summarise(r.id))
            rec["ring"] = rk.get(r.id, [0, 0])
            rec["province_id"] = pmap.get(r.id)
            rec["flags"] = flag_counts(members.get(r.id, []))
            rec["bbox"] = [round(float(v), 4) for v in r.geometry.bounds]
            area_rows.append(rec)

    D = os.path.join(out, "data")
    season = {"start": str(start), "end": str(end), "months": months, "first_year": first, "current": cur_key,
              "label": f"{start.day} {start:%b}–{end.day} {end:%b %Y}" if start.year == end.year else f"{start.day} {start:%b %Y}–{end.day} {end:%b %Y}",
              "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    cfg_pub = {k: CFG.get(k) for k in ("title", "subtitle", "credit", "ring_km", "map_bounds", "programmes")}
    cfg_pub["flags"] = [{"key": k, "label": l} for k, l, _ in FLAGS]
    json.dump({"season": season, "config": cfg_pub, "national": national, "provinces": prov_rows, "districts": dist_rows, "areas": area_rows},
              open(os.path.join(D, "stats.json"), "w"), separators=(",", ":"))
    pidx = {r.id: i for i, r in enumerate(prov.itertuples())}
    pmap = U[U.unit.isin(pidx)].drop_duplicates("i").set_index("i").unit.map(pidx)
    d0 = pd.Timestamp(start)
    days = (pd.to_datetime(P.alert__date.str[:10]) - d0).dt.days.values
    rows = [[round(float(x), 4), round(float(y), 4), int(d), int(f), int(pmap.get(i, -1))]
            for i, (x, y, d, f) in enumerate(zip(P.longitude, P.latitude, days, P["fl"]))]
    json.dump({"start": str(start), "fields": ["lon", "lat", "day", "flags", "province"], "low_bit": LOWBIT, "rows": rows},
              open(os.path.join(D, "fires.json"), "w"), separators=(",", ":"))

    def save_geo(g, name, tol, nd=4):
        g = g.copy()
        g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
        gj = json.loads(g.to_json(drop_id=True))
        def rnd(c):
            return [round(c[0], nd), round(c[1], nd)] if isinstance(c[0], (int, float)) else [rnd(x) for x in c]
        for f in gj["features"]:
            f["geometry"]["coordinates"] = rnd(f["geometry"]["coordinates"])
        json.dump(gj, open(os.path.join(D, name), "w"), separators=(",", ":"))
    save_geo(prov[["id", "name", "geometry"]], "provinces.geojson", 0.004)
    save_geo(dist[["id", "name", "province_id", "geometry"]], "districts.geojson", 0.003)

    # downloads
    W = os.path.join(out, "downloads")
    pname = dict(zip(prov.id, prov.name))
    def flat(rows, kind, extra=()):
        recs = []
        for r in rows:
            rec = {"id": r["id"], "name": r["name"], "type": kind}
            for k in extra:
                v = r.get(k)
                rec[k] = v[0] if isinstance(v, list) else v
            rec.update({"alerts_now": r["now"][0], "alerts_now_excl_low": r["now"][1], "avg_earlier_years": r["mean"][0],
                        "ratio": r["ratio"][0], "rank_since_first_year": r["rank"][0], "alerts_2015": r["y2015"][0]})
            for m in months:
                rec[f"alerts_{m}"] = r["monthly"][m][0]
            recs.append(rec)
        return recs
    tables = (flat(prov_rows, "province", ("geostore",)) + flat(dist_rows, "district", ("province_id", "area_km2", "geostore"))
              + flat([r for r in area_rows if r["kind"] == "pa"], "protected area", ("desig", "iucn", "status_yr", "area_ha", "ring", "province_id", "geostore"))
              + flat([r for r in area_rows if r["kind"] == "kba"], "KBA", ("status", "area_ha", "ring", "province_id", "geostore")))
    df = pd.DataFrame(tables)
    df["province"] = df.province_id.map(pname) if "province_id" in df else None
    df.to_csv(os.path.join(W, "area_summary.csv"), index=False)
    yrows = [{"id": r["id"], "name": r["name"], **{y: v[0] for y, v in r["years"].items()}} for r in [national] + prov_rows + dist_rows + area_rows]
    pd.DataFrame(yrows).to_csv(os.path.join(W, "alerts_by_year_same_period.csv"), index=False)
    pts = P.drop(columns=["fl"]).copy()
    pts["province"] = pmap.reindex(range(len(P))).map({v: pname[k] for k, v in pidx.items()}).values
    pts.to_csv(os.path.join(W, "viirs_alerts_season.csv"), index=False)
    log("data written to", out)


if __name__ == "__main__":
    main()
