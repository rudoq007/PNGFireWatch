"""Maps and PDF reports for PNG Fire Watch, made from the data written by update.py.

    python pipeline/outputs.py --out _site
"""
import argparse
import json
import os
import sys
import textwrap
import warnings

import geopandas as gpd
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))
from common import fetch_kbas, fetch_protected_areas, load_admin  # noqa: E402

GREY, ACC, INK, BAND = "#9a9a92", "#c0392b", "#1d2327", "#2b2118"
MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MNL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
IMG = "Imagery: Esri, Maxar, Earthstar Geographics."


def fmt(v, d=0):
    return "–" if v is None else f"{v:,.{d}f}"


def rtxt(r, i=0):
    return ("new" if r["now"][i] else "–") if r["ratio"][i] is None else f"{r['ratio'][i]:.1f}×"


def mlabel(m, season=None, long=False):
    lab = (MNL if long else MN)[int(m[5:]) - 1]
    if season and m == season["end"][:7] and int(season["end"][8:]) < 28:
        lab = f"1–{int(season['end'][8:])} {lab}"
    return lab


class Ctx:
    def __init__(self, out):
        self.out = out
        self.st = json.load(open(os.path.join(out, "data", "stats.json")))
        self.season = self.st["season"]
        self.months = self.season["months"]
        cm = plt.get_cmap("YlOrRd")
        n = len(self.months)
        self.mcol = {m: matplotlib.colors.to_hex(cm(0.25 + 0.7 * i / max(1, n - 1))) for i, m in enumerate(self.months)}
        self.src = (f"Fire alerts: NASA VIIRS (S-NPP) via Global Forest Watch, to {self.season['end']}. Boundaries: OCHA COD-AB / PNG NSO (CC BY-IGO); "
                    f"protected areas: WDPA (UNEP-WCMC & IUCN); KBAs: BirdLife International. Generated automatically by {self.st['config']['credit']}, {self.season['updated']}.")
        pts = pd.read_csv(os.path.join(out, "downloads", "viirs_alerts_season.csv"))
        self.P = pts
        self.prov, self.dist = load_admin()
        self.pas, self.kbas = fetch_protected_areas(), fetch_kbas()
        self.country = gpd.read_file(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "inputs", "country.geojson"))
        self.PR = {r["id"]: r for r in self.st["provinces"]}
        for d in ("maps/overview", "maps/provinces", "reports/provinces"):
            os.makedirs(os.path.join(out, d), exist_ok=True)


def bars(ax, keys, vals, hl, title, ylab, ref=None, reflab=None, lab=lambda k: k, valfmt=lambda v: f"{v:,.0f}"):
    x = np.arange(len(keys))
    ax.bar(x, vals, color=[ACC if h else GREY for h in hl], width=0.7)
    for i, (v, h) in enumerate(zip(vals, hl)):
        if h and v > 0:
            ax.text(i, v, valfmt(v), ha="center", va="bottom", fontsize=7.5, color=INK)
    if ref:
        ax.axhline(ref, color="#555", ls="--", lw=0.8)
        ax.text(-0.4, ref, f"{reflab} ", va="bottom", ha="left", fontsize=7, color="#555")
    step = max(1, len(keys) // 13)
    ax.set_xticks(x[::step], [lab(k) for k in keys][::step], fontsize=7)
    ax.tick_params(axis="y", labelsize=7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_title(title, loc="left", fontsize=9.5, color=INK, fontweight="bold")
    ax.set_ylabel(ylab, fontsize=7.5)


def header(fig, title, sub):
    fig.patches.append(plt.Rectangle((0, 0.93), 1, 0.07, transform=fig.transFigure, color=BAND, zorder=0))
    fig.text(0.05, 0.965, title, fontsize=16, color="white", fontweight="bold", va="center")
    fig.text(0.05, 0.94, sub, fontsize=8.5, color="#f0dccb", va="center")


def kpis(fig, items, y=0.83):
    w = 0.9 / len(items)
    for i, (lab, val, note) in enumerate(items):
        x = 0.05 + i * w
        fig.text(x, y + 0.045, lab, fontsize=7, color="#555")
        fig.text(x, y + 0.012, val, fontsize=15, color=INK, fontweight="bold")
        fig.text(x, y - 0.008, note, fontsize=6.5, color="#666")


def table(ax, rows, cols, widths=None, fs=7.0):
    ax.axis("off")
    if not rows:
        ax.text(0, 0.9, "None", fontsize=8, color="#666")
        return
    t = ax.table(cellText=rows, colLabels=cols, loc="upper center", cellLoc="left", colWidths=widths)
    t.auto_set_font_size(False); t.set_fontsize(fs); t.scale(1, 1.15)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor("#dddddd")
        if r == 0:
            cell.set_facecolor("#f1ece6"); cell.set_text_props(fontweight="bold")


def scatter_months(ax, c, P, size):
    for m in c.months:
        s = P[P.alert__date.str[:7] == m]
        ax.scatter(s.longitude, s.latitude, s=size, c=c.mcol[m], lw=0, alpha=0.85, zorder=3)


def national_map(c):
    fig, ax = plt.subplots(figsize=(14, 10))
    c.country.plot(ax=ax, color="#f2f0ea", edgecolor="#999", lw=0.5)
    scatter_months(ax, c, c.P, 1.0)
    c.prov.boundary.plot(ax=ax, color="#333", lw=0.8, zorder=5)
    for r in c.prov.itertuples():
        n = c.PR[r.id]["now"][0]
        if n >= 200:
            pt = r.geometry.representative_point()
            ax.text(pt.x, pt.y, f"{r.name.replace(' Province', '')}\n{n:,}", fontsize=7.5, ha="center", va="center", zorder=6,
                    bbox=dict(fc="white", alpha=0.7, lw=0, pad=1))
    b = c.country.total_bounds
    ax.set_xlim(b[0] - 0.2, b[2] + 0.2); ax.set_ylim(b[1] - 0.2, b[3] + 0.2); ax.set_aspect("equal"); ax.tick_params(labelsize=8); ax.set_xlabel(""); ax.set_ylabel("")
    h = [Line2D([], [], marker="o", ls="", mfc=c.mcol[m], mec="none", ms=7, label=mlabel(m, c.season, True)) for m in c.months]
    ax.legend(handles=h, title="VIIRS fire alerts", loc="upper right", fontsize=9, framealpha=0.9)
    ax.set_title(f"Papua New Guinea: fire alerts, {c.season['label']}", loc="left", fontsize=15, fontweight="bold")
    fig.text(0.01, 0.01, textwrap.fill("Labels: provinces with 200 or more alerts. " + c.src, 230), fontsize=7, color="#555")
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(os.path.join(c.out, "maps/overview/national.png"), dpi=120); plt.close(fig)


def province_map(c, ax, pid, imagery=True):
    import contextily as cx
    g = c.prov[c.prov.id == pid].to_crs(3857)
    b = g.total_bounds; w, h = b[2] - b[0], b[3] - b[1]; s = max(w, h * 1.25) * 1.06
    cxx, cyy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    ax.set_xlim(cxx - s / 2, cxx + s / 2); ax.set_ylim(cyy - s / 2 / 1.25, cyy + s / 2 / 1.25)
    if imagery:
        try:
            cx.add_basemap(ax, source=cx.providers.Esri.WorldImagery, attribution=False, zoom="auto")
        except Exception as e:
            print("basemap failed", pid, e); ax.set_facecolor("#dfe3dc")
    c.dist[c.dist.province_id == pid].to_crs(3857).boundary.plot(ax=ax, color="white", lw=0.7, zorder=4)
    for gg, col in ((c.pas, "#18c0a8"), (c.kbas, "#5aa8ff")):
        if len(gg):
            sub = gg.to_crs(3857)
            sub = sub[sub.intersects(g.iloc[0].geometry)]
            if len(sub):
                sub.boundary.plot(ax=ax, color=col, lw=1.0, zorder=5)
    g.boundary.plot(ax=ax, color="#ffe066", lw=2, zorder=6)
    P = c.P[c.P.province == c.PR[pid]["name"]]
    if len(P):
        pts = gpd.GeoSeries(gpd.points_from_xy(P.longitude, P.latitude), crs=4326).to_crs(3857)
        mon = P.alert__date.str[:7].values
        for m in c.months:
            sel = mon == m
            if sel.any():
                ax.scatter(pts.x[sel], pts.y[sel], s=3 if len(P) > 3000 else 8, c=c.mcol[m], edgecolors="black", linewidths=0.2, zorder=7)
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    hh = [Line2D([], [], color="#ffe066", lw=2, label="Province"), Line2D([], [], color="white", lw=1, label="District"),
          Line2D([], [], color="#18c0a8", lw=1.2, label="Protected area (WDPA)"), Line2D([], [], color="#5aa8ff", lw=1.2, label="Key Biodiversity Area")]
    hh += [Line2D([], [], marker="o", ls="", mfc=c.mcol[m], mec="k", mew=0.3, ms=5, label=mlabel(m, c.season, True)) for m in c.months]
    lg = ax.legend(handles=hh, loc="lower left", fontsize=6.5, framealpha=0.85); lg.set_zorder(30)


def province_outputs(c, pr):
    pid, name = pr["id"], pr["name"]
    fig, ax = plt.subplots(figsize=(11, 8.8)); province_map(c, ax, pid)
    ax.set_title(f"{name}: fire alerts, {c.season['label']}", loc="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.01, textwrap.fill(IMG + " " + c.src, 200), fontsize=6.3, color="#555")
    fig.tight_layout(rect=(0, 0.03, 1, 1)); fig.savefig(os.path.join(c.out, f"maps/provinces/{pid}.jpg"), dpi=115, pil_kwargs={"quality": 84}); plt.close(fig)
    yk = sorted(pr["years"]); cur = c.season["current"]
    with PdfPages(os.path.join(c.out, f"reports/provinces/{pid}.pdf")) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69)); header(fig, name, f"Fire situation, {c.season['label']} · updated {c.season['updated']}")
        bm = max(pr["monthly"], key=lambda m: pr["monthly"][m][0]) if pr["monthly"] else None
        kpis(fig, [(f"Fire alerts\n{c.season['label']}", fmt(pr["now"][0]), f"excl. low confidence: {pr['now'][1]:,}"),
                   ("Versus average\nof earlier years", rtxt(pr), f"average {pr['mean'][0]:,.0f}"),
                   ("Versus 2015 El Niño", f"{100 * pr['now'][0] / pr['y2015'][0]:.0f}%" if pr["y2015"][0] else "–", f"2015: {pr['y2015'][0]:,}"),
                   ("Busiest month", mlabel(bm, c.season, True) if bm else "–", f"{pr['monthly'][bm][0]:,} alerts" if bm else "")])
        ax = fig.add_axes([0.05, 0.29, 0.9, 0.5]); province_map(c, ax, pid)
        f = pr["flags"]; tot = max(1, f.get("_base", pr["now"])[0])
        lines = [f"{lab}: {f[k][0]:,} ({100 * f[k][0] / tot:.0f}%)" for k, lab in [(x["key"], x["label"]) for x in c.st["config"]["flags"]] if f[k][0]]
        fig.text(0.05, 0.265, "Where the alerts fall", fontsize=10, fontweight="bold", color=INK)
        fig.text(0.05, 0.25, textwrap.fill(" · ".join(lines) or "No alerts this season.", 125), fontsize=7.6, va="top", color=INK, linespacing=1.5)
        ds = sorted([d for d in c.st["districts"] if d["province_id"] == pid], key=lambda d: -d["now"][0])
        ax = fig.add_axes([0.05, 0.03, 0.9, 0.16]); ax.set_title("Districts", loc="left", fontsize=9.5, fontweight="bold")
        table(ax, [[d["name"].replace(" District", "")] + [f"{d['monthly'][m][0]:,}" for m in c.months] + [f"{d['now'][0]:,}", rtxt(d), f"{d['density'][0]:.1f}"] for d in ds],
              ["District"] + [mlabel(m) for m in c.months] + ["Total", "vs avg", "per 100 km²"],
              [0.28] + [0.68 / (len(c.months) + 3)] * (len(c.months) + 3))
        pdf.savefig(fig); plt.close(fig)
        fig = plt.figure(figsize=(8.27, 11.69)); header(fig, name, "Trends, protected areas and Key Biodiversity Areas")
        ax = fig.add_axes([0.1, 0.68, 0.84, 0.19])
        bars(ax, yk, [pr["years"][y][0] for y in yk], [y == cur for y in yk], f"Fire alerts in the same period ({c.season['label'][:-5]}) of each year", "alerts",
             ref=pr["mean"][0] or None, reflab="average of earlier years", lab=lambda k: "'" + k[2:])
        mo = list(pr["monthly"]); vals = [pr["monthly"][m][0] for m in mo]; mx = max(vals) if vals else 0
        ax = fig.add_axes([0.1, 0.45, 0.84, 0.16])
        bars(ax, mo, vals, [v == mx and v > 0 for v in vals], "Fire alerts by month this season", "alerts", lab=lambda k: mlabel(k))
        ar = sorted([a for a in c.st["areas"] if a.get("province_id") == pid], key=lambda a: -a["now"][0])
        ax = fig.add_axes([0.05, 0.04, 0.9, 0.34]); ax.set_title("Protected areas and KBAs (main province)", loc="left", fontsize=9.5, fontweight="bold")
        table(ax, [[a["name"][:38], "PA" if a["kind"] == "pa" else "KBA", f"{a['area_ha']:,.0f}", f"{a['now'][0]:,}", f"{a['mean'][0]:,.1f}", rtxt(a), f"{a['ring'][0]:,}"] for a in ar[:24]],
              ["Name", "Type", "Area (ha)", "Fires now", "Avg", "vs avg", f"0–{c.st['config']['ring_km']} km"], [0.4, 0.07, 0.12, 0.1, 0.09, 0.09, 0.1], fs=6.6)
        fig.text(0.05, 0.015, textwrap.fill(c.src, 160), fontsize=5.8, color="#666")
        pdf.savefig(fig); plt.close(fig)


def summary_report(c):
    st = c.st; N = st["national"]; lab = c.season["label"]; cur = c.season["current"]
    provs = sorted(st["provinces"], key=lambda p: -p["now"][0])
    areas = st["areas"]
    above = [a for a in areas if a["now"][0] > a["mean"][0] and a["now"][0] > 0]
    rec = [a for a in areas if a["rank"][0] == 1 and a["now"][0] > 0]
    with PdfPages(os.path.join(c.out, "reports/fire_situation_summary.pdf")) as pdf:
        fig = plt.figure(figsize=(8.27, 11.69))
        header(fig, "PNG fire situation summary", f"Papua New Guinea · {lab} · updated {c.season['updated']}")
        kpis(fig, [(f"Fire alerts, national\n{lab}", fmt(N["now"][0]), f"excl. low confidence: {N['now'][1]:,}"),
                   ("Versus average\nof earlier years", rtxt(N), f"2015 El Niño: {N['y2015'][0]:,}"),
                   ("Provinces above\naverage", f"{sum(1 for p in provs if p['now'][0] > p['mean'][0])} of {len(provs)}", ""),
                   ("Protected areas and\nKBAs above average", f"{len(above)} of {len(areas)}", f"{len(rec)} at a record")])
        f = N["flags"]; tot = max(1, f.get("_base", N["now"])[0])
        pts = [f"Most alerts: " + ", ".join(f"{p['name'].replace(' Province', '')} ({p['now'][0]:,})" for p in provs[:4]) + "."]
        pts.append(f"{100 * f['tcd30'][0] / tot:.0f}% of alerts are in areas with over 30% tree cover in 2000, {100 * f['primary'][0] / tot:.0f}% in primary forest and "
                   f"{100 * f['ifl'][0] / tot:.0f}% in intact forest landscapes.")
        extra = [f"{f[k][0]:,} {lab}" for k, lab in (("mangrove", "in mangroves"), ("peat", "on peatland")) if f[k][0]]
        pts.append(f"{f['pa'][0]:,} alerts fall in protected areas and {f['kba'][0]:,} in Key Biodiversity Areas" + ("; " + " and ".join(extra) if extra else "") + ".")
        hi = sorted([p for p in provs if p["ratio"][0]], key=lambda p: -p["ratio"][0])[:4]
        if hi:
            pts.append("Furthest above normal: " + ", ".join(f"{p['name'].replace(' Province', '')} ({p['ratio'][0]:.1f}×)" for p in hi) + ".")
        y0 = 0.765; fig.text(0.05, y0 + 0.012, "Key points", fontsize=10, fontweight="bold", color=INK)
        for t in pts:
            w = textwrap.fill("•  " + t, 112, subsequent_indent="    ")
            fig.text(0.05, y0, w, fontsize=8, va="top", color=INK); y0 -= 0.017 * (w.count("\n") + 1) + 0.006
        im = plt.imread(os.path.join(c.out, "maps/overview/national.png"))
        ax = fig.add_axes([0.03, 0.06, 0.94, y0 - 0.08]); ax.imshow(im); ax.axis("off")
        fig.text(0.05, 0.025, textwrap.fill(c.src, 150), fontsize=6, color="#666")
        pdf.savefig(fig); plt.close(fig)
        fig = plt.figure(figsize=(8.27, 11.69)); header(fig, "PNG fire situation summary", "National trend and provinces")
        yk = sorted(N["years"]); ax = fig.add_axes([0.1, 0.69, 0.84, 0.18])
        bars(ax, yk, [N["years"][y][0] for y in yk], [y == cur for y in yk], f"National fire alerts in the same period ({lab[:-5]}) of each year", "alerts",
             ref=N["mean"][0], reflab="average of earlier years", lab=lambda k: "'" + k[2:])
        ax = fig.add_axes([0.05, 0.04, 0.9, 0.58]); ax.set_title("Provinces", loc="left", fontsize=9.5, fontweight="bold")
        table(ax, [[p["name"].replace(" Province", "")] + [f"{p['monthly'][m][0]:,}" for m in c.months] + [f"{p['now'][0]:,}", f"{p['mean'][0]:,.0f}", rtxt(p), str(p["rank"][0])] for p in provs],
              ["Province"] + [mlabel(m) for m in c.months] + ["Total", "Avg", "vs avg", "Rank"],
              [0.26] + [0.66 / (len(c.months) + 4)] * (len(c.months) + 4), fs=6.8)
        pdf.savefig(fig); plt.close(fig)
        fig = plt.figure(figsize=(8.27, 11.69)); header(fig, "PNG fire situation summary", "Protected areas and Key Biodiversity Areas with the most fire")
        pn = {p["id"]: p["name"].replace(" Province", "") for p in st["provinces"]}
        top = sorted([a for a in areas if a["now"][0] > 0], key=lambda a: -a["now"][0])[:40]
        ax = fig.add_axes([0.04, 0.05, 0.92, 0.85])
        table(ax, [[a["name"][:34], "PA" if a["kind"] == "pa" else "KBA", pn.get(a.get("province_id"), "")[:18], f"{a['now'][0]:,}", f"{a['mean'][0]:,.1f}", rtxt(a), f"{a['ring'][0]:,}"] for a in top],
              ["Name", "Type", "Province", "Fires now", "Avg", "vs avg", f"0–{st['config']['ring_km']} km"], [0.34, 0.07, 0.2, 0.1, 0.08, 0.09, 0.1], fs=6.5)
        fig.text(0.05, 0.02, textwrap.fill("The 40 protected areas and KBAs with the most alerts this season. " + c.src, 160), fontsize=6, color="#666")
        pdf.savefig(fig); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default="_site"); a = ap.parse_args()
    c = Ctx(a.out)
    national_map(c); print("national map done", flush=True)
    for pr in c.st["provinces"]:
        province_outputs(c, pr)
    print("province maps and reports done", flush=True)
    summary_report(c); print("summary done", flush=True)


if __name__ == "__main__":
    main()
