# PNG Fire Watch

PNG Fire Watch is a live, national fire dashboard for Papua New Guinea. It updates itself twice a day.

It shows:

- **Fire alerts:** every NASA VIIRS fire alert of the current fire season on an interactive map.
- **Areas:** every province (22), district (87), protected area (WDPA) and Key Biodiversity Area. Each one is compared with the same calendar period in every year since 2012.
- **Where fires fall:** whether each alert is in forest, primary forest, intact forest landscapes, protected areas, KBAs or mangroves.
- **Downloads:** a national summary and 22 province reports (PDF), maps, and data tables.

The dashboard uses only national and global datasets that cover the whole country. Project-specific layers are not included.

## How it works

```
GitHub Actions (twice a day)
  pipeline/update.py    -> GFW Data API: latest VIIRS alerts for PNG, protected areas, KBAs
                           counts per area, comparison with inputs/history.json
                           writes _site/data/*.json and _site/downloads/*.csv
  pipeline/outputs.py   -> national map, 22 province maps (Esri imagery), PDF reports
  deploy                -> GitHub Pages
```

- **Skipping runs.** If the live site already has the latest alert date, the run stops early. Set the repository variable `SITE_URL` to enable this.
- **History baseline.** `inputs/history.json` holds daily alert counts per area since 2012 for every day before the current season. When a new season starts (1 July by default), `update.py` adds the season just finished and commits the file.
- **Failure alerts.** If a run fails, the workflow opens a GitHub issue labelled `update-failure`, so repository watchers get an email. The site keeps showing the last good update.

## Set up

1. **Create the repository.** Make a public repository, for example `png-firewatch`. Upload everything in this folder, including the hidden `.github` folder.
2. **Turn on Pages.** Go to **Settings → Pages** and set Source to **GitHub Actions**.
3. **Add the API key (required for dependable runs).**
   1. Get a free key from Global Forest Watch: create an account at globalforestwatch.org, then follow https://data-api.globalforestwatch.org/#tag/Authentication.
   2. Go to **Settings → Secrets and variables → Actions → New repository secret**. Name it `GFW_API_KEY` and paste the key.
   - Without a key, the pipeline falls back to the Global Nature Watch proxy. It works today, but it is not a documented public service.
4. **Add the site address.** Under **Settings → Secrets and variables → Actions → Variables**, add `SITE_URL`, for example `https://<owner>.github.io/png-firewatch`.
5. **Get failure emails.** Click **Watch → All activity** on the repository, or at least **Issues**, so failure issues reach you by email.
6. **First run.** Go to **Actions → Update PNG Fire Watch → Run workflow**, tick **force**, and run it. It takes about 10–15 minutes.

## Configuration (`config.json`)

| Key | Meaning |
|---|---|
| `title`, `subtitle`, `credit` | Text in the page header, maps and reports |
| `iso3` | Country code used in every GFW query (`PNG`) |
| `season_start` | First day of the fire season (`MM-DD`, default `07-01`) |
| `baseline_first_year` | First year of comparison (2012, the first full VIIRS year) |
| `ring_km` | Ring outside protected areas and KBAs where nearby fires are counted |
| `map_bounds` | Initial map extent `[[south, west], [north, east]]` |
| `programmes` | Optional groups of provinces shown as filter buttons. Each has `id`, `name`, `description` and `provinces` as OCHA pcodes. Delete them all to hide the filter. |

For example, to add a programme covering the Sepik provinces:

```json
{"id": "sepik", "name": "Sepik", "description": "East and West Sepik", "provinces": ["PG14", "PG15"]}
```

## Data, licences and how they are used

| Data | Source | Licence / terms | How it is used |
|---|---|---|---|
| Fire alerts | NASA FIRMS VIIRS VNP14IMGT (Suomi NPP only), via GFW Data API | NASA open data | Queried daily; season points published in `data/fires.json` and CSV |
| Provinces, districts | OCHA COD-AB / PNG National Statistical Office (2019) | CC BY-IGO | Stored in `inputs/`, published simplified |
| Protected areas | WDPA (UNEP-WCMC & IUCN), via GFW | WDPA terms of use | Boundaries fetched at run time to count alerts. They are **not** stored in the repository or published as files. The map shows them from GFW's tile service |
| Key Biodiversity Areas | World Database of KBAs (BirdLife), via GFW | Non-commercial; no redistribution | Same as protected areas |
| Intact forest landscapes, primary forest | intactforests.org; UMD | CC BY 4.0 | GFW tile services; also flags on each alert |
| Peatlands, mangroves | GFW peatlands; Global Mangrove Watch | See GFW | GFW tile services; mangrove flag on each alert |
| Imagery, street map | Esri World Imagery; OpenStreetMap | Attribution required | Web basemaps and province maps. Attribution is printed on every map |

Notes:

- **Satellites.** The VIIRS series in GFW comes from Suomi NPP only. NOAA-20 and NOAA-21 alerts are not included, so adding satellites does not inflate recent years compared with 2012–2016.
- **Confidence filter.** The dashboard can exclude low-confidence alerts. Both versions are computed for every number.
- **Land-class flags.** The flags are GFW's own attributes on each alert. The peatland, mining and LandMark flags have little or no coverage for PNG, so they are left out of charts when empty.
- **Licence review.** Check the WDPA and KBA terms before any commercial use.

## Maintenance

- **Adding or changing areas.** Admin boundaries live in `inputs/provinces.geojson` and `inputs/districts.geojson`. If you replace them, delete `inputs/history.json` and `inputs/geostores.json` and run once with force. History is rebuilt in about 5 minutes.
- **New GFW data versions.** Protected areas, KBAs and alerts are always read from `latest`, so new versions appear on their own. If a new WDPA version adds areas, run `python pipeline/history.py` after deleting `inputs/history.json` to add their history.
- **Run locally.**
  ```
  pip install -r pipeline/requirements.txt
  python pipeline/update.py --out _site --force
  python pipeline/outputs.py --out _site
  cp index.html _site/ && cd _site && python -m http.server 8000
  ```

## Files

| Path | Contents |
|---|---|
| `index.html` | The whole web app: Leaflet map, charts, tables, downloads |
| `config.json` | Settings (see above) |
| `inputs/` | Admin boundaries, history baseline, GFW geostore ids for provinces and districts |
| `pipeline/common.py` | GFW queries, area loading, aggregation |
| `pipeline/history.py` | Builds and extends the history baseline |
| `pipeline/update.py` | Daily data update |
| `pipeline/outputs.py` | Maps and PDF reports |
| `.github/workflows/update.yml` | Schedule, build, deploy and failure notification |
