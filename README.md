# AeroGuard

**Explainable grid-based coastal impact forecasting and decision support for Shyamnagar / Gabura, Bangladesh**

Fabliha Afia (2105096) · Department of Computer Science and Engineering, BUET · Undergraduate thesis, 2026

AeroGuard does not produce its own weather forecast. It turns existing 1–7 day forecasts,
historical rainfall, Sentinel-1 observed water, terrain and polder/drainage data into local
impact on a 500 m grid: where significant temporary inundation is likely, how long water may
persist, which roads, clinics and shelters lose access, and where intervention is most urgent,
each with a confidence level and its dominant drivers.

## Scope

| Item | Decision |
|---|---|
| Study area | Shyamnagar Upazila (12 unions + Sundarbans range, flagged); Gabura union as detailed pilot |
| Spatial unit | 500 m × 500 m grid (UTM 45N, EPSG:32645) |
| Forecast horizon | Day 1–3 and Day 5–7, evaluated separately |
| Core target | Significant temporary inundation (0/1) and temporary water fraction (0–1) |
| Extension 1 | Water persistence class and recovery-time proxy |
| Extension 2 | Road / clinic / shelter access disruption, exposed population and cropland |
| Output | Risk, confidence, dominant drivers (SHAP) and intervention priority dashboard |

## Repository layout

```
00_documentation/        data inventory, download manifest (source URL + sha256)
01_bronze_raw/           raw downloads, clipped to the study area (not committed; reproducible)
02_silver_clean/         cleaned, projected layers: boundary, grid, roads, facilities, ...
03_gold_analysis_ready/  static/dynamic features, labels, master dataset, impacts
04_qgis_project/         QGIS project for visual QA
05_scripts/              numbered pipeline steps (run in order)
06_outputs/              maps, figures, model outputs
references/              reading list (PDFs kept locally)
config.yaml              all paths, CRS, grid size, thresholds
```

## Pipeline status

| Step | Script | Status |
|---|---|---|
| 1. Clean study-area boundary, unions, Gabura pilot | `01_make_boundary.py` | done |
| 2. 500 m grid | `02_make_grid.py` | done (6,851 cells: 1,881 settled, 4,970 Sundarbans, 173 Gabura) |
| 3. Static layers: DEM, JRC water, WorldCover, WorldPop, OSM | `03_download_static.py` | rasters + roads done; OSM facilities/water pending |
| 4. Static features per grid cell | | next |
| 5. Rainfall / ERA5-Land daily features | | planned |
| 6. Sentinel-1 flood labels | | planned |
| 7. Master dataset, models, SHAP, persistence, impact, dashboard | | planned |

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
cd 05_scripts
python 01_make_boundary.py
python 02_make_grid.py
python 03_download_static.py
```

All data sources are open and need no login (Copernicus DEM, JRC Global Surface Water,
ESA WorldCover, WorldPop, OpenStreetMap, geoBoundaries/BBS-OCHA union boundaries).

## Data notes

- The OSM upazila polygon extends ~1,000 km² into the Bay of Bengal, so the study area is
  the union of the land units from BBS/OCHA boundaries.
- Copernicus GLO-30 is a surface model (includes trees and buildings); a bare-earth DEM
  should be considered for the low-lying polders.
