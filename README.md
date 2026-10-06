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
| 3. Static layers: DEM, JRC water, WorldCover, WorldPop, OSM roads | `03_download_static.py` | done |
| 3b. OSM facilities, waterways, embankments, sluices (Geofabrik extract) | `03b_osm_features.py` | done |
| 4. Static features per grid cell (31 features) | `04_static_features.py` | done |
| 5. CHIRPS daily rainfall + ERA5/ERA5-Land daily, 2015–2025 | `05_download_climate.py` | running (ERA5 rate-limited by Open-Meteo; resumable) |
| 6. Sentinel-1 RTC VV/VH stack, 20 m, settled unions | `06_sentinel1_stack.py` | tested on Amphan window; full archive pending |
| 7. Flood labels (change detection + calibration on non-event dates) | | next |
| 8. Dynamic features, master dataset, models, SHAP, persistence, impact, dashboard | | planned |

### First label check: Cyclone Amphan (landfall 20 May 2020)

Prototype change detection (VV below an Otsu threshold, water in the post image but not in
the same-orbit pre-event image, JRC permanent water removed). Mean temporary-water share of
cells, by union:

| Union | 16 May (pre) | 22 May | 28 May | 9 Jun |
|---|---|---|---|---|
| Gabura | 0.0 % | **17.2 %** | **17.0 %** | 11.6 % |
| Buri Goalini | 0.0 % | 14.1 % | 10.5 % | 10.6 % |
| Padma Pukur | 0.0 % | 13.8 % | 11.8 % | 11.4 % |
| Bhurulia | 0.0 % | 4.2 % | 3.4 % | 5.1 % |

Gabura (embankment breaches during Amphan) ranks highest and stays inundated, as expected.
A 5–10 % background change in every union (monsoon onset, aquaculture ghers, speckle)
must be calibrated on non-event dates before these become training labels.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
cd 05_scripts
python 01_make_boundary.py
python 02_make_grid.py
python 03_download_static.py
python 03b_osm_features.py
python 04_static_features.py
python 05_download_climate.py
python 06_sentinel1_stack.py          # or one window: python 06_sentinel1_stack.py 2020-05-10 2020-06-12
```

All data sources are open and need no login (Copernicus DEM, JRC Global Surface Water,
CHIRPS, ERA5 via Open-Meteo, Sentinel-1 RTC via Microsoft Planetary Computer,
ESA WorldCover, WorldPop, OpenStreetMap, geoBoundaries/BBS-OCHA union boundaries).

## Data notes

- The OSM upazila polygon extends ~1,000 km² into the Bay of Bengal, so the study area is
  the union of the land units from BBS/OCHA boundaries.
- About 45–47 % of the settled area is water in WorldCover / JRC (shrimp and fish ghers), so
  "temporary water" must be measured against a same-orbit Sentinel-1 reference, not only
  the JRC permanent-water mask.
- Copernicus GLO-30 is a surface model (includes trees and buildings); a bare-earth DEM
  should be considered for the low-lying polders.
