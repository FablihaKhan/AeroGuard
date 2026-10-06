"""Step 5 - Download daily climate history 2015-2025 (no login needed).

1. CHIRPS v2.0 daily rainfall (0.05 deg): only the study-area window is read from the
   cloud-optimised GeoTIFFs, one file per day, stacked per year into
   01_bronze_raw/rainfall/chirps_daily_{year}.nc  (dims: time, lat, lon; mm/day)
2. ERA5 / ERA5-Land daily via the Open-Meteo archive API on a 0.1 deg point lattice:
   01_bronze_raw/era5_land/era5_daily_{year}.csv  (point_id, lat, lon, date, variables)

Both are resumable: finished years are skipped.
"""

import datetime as dt
import os
import time
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
import xarray as xr
from rasterio.windows import from_bounds

from common import BRONZE, CONFIG, CRS_GEO, SILVER, USER_AGENT, get_logger

log = get_logger("05_climate")

os.environ.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".cog",
                  GDAL_HTTP_MAX_RETRY="5", GDAL_HTTP_RETRY_DELAY="2")
CHIRPS = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/cogs/p05/{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.cog"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
# Rainfall upstream of the study area matters too, so the window is padded by 0.25 deg.
PAD_DEG = 0.25
ERA5_LAND_VARS = ["soil_moisture_0_to_7cm_mean", "soil_moisture_7_to_28cm_mean", "temperature_2m_mean"]
ERA5_VARS = ["precipitation_sum", "rain_sum", "et0_fao_evapotranspiration", "wind_speed_10m_max",
             "wind_gusts_10m_max", "wind_direction_10m_dominant"]


def study_bounds():
    study = gpd.read_file(SILVER / "boundary" / "study_area_v01.gpkg").to_crs(CRS_GEO)
    w, s, e, n = study.total_bounds
    return w - PAD_DEG, s - PAD_DEG, e + PAD_DEG, n + PAD_DEG


def read_chirps_day(day, bounds):
    url = "/vsicurl/" + CHIRPS.format(y=day.year, m=day.month, d=day.day)
    for attempt in range(4):
        try:
            with rasterio.open(url) as src:
                win = from_bounds(*bounds, transform=src.transform).round_offsets().round_lengths()
                arr = src.read(1, window=win).astype("float32")
                arr[arr < 0] = np.nan                     # -9999 = no data (sea)
                return day, arr, src.window_transform(win)
        except rasterio.errors.RasterioIOError:
            time.sleep(2 * (attempt + 1))
    log.warning("CHIRPS missing: %s", day)
    return day, None, None


def chirps(bounds, years):
    out_dir = BRONZE / "rainfall"
    out_dir.mkdir(parents=True, exist_ok=True)
    for year in years:
        out = out_dir / f"chirps_daily_{year}.nc"
        if out.exists():
            log.info("cached: %s", out.name)
            continue
        days = pd.date_range(f"{year}-01-01", f"{year}-12-31").date
        with ThreadPoolExecutor(16) as pool:
            results = sorted(pool.map(lambda d: read_chirps_day(d, bounds), days), key=lambda r: r[0])
        good = [r for r in results if r[1] is not None]
        transform, shape = good[0][2], good[0][1].shape
        cube = np.full((len(days), *shape), np.nan, dtype="float32")
        for i, (_, arr, _) in enumerate(results):
            if arr is not None:
                cube[i] = arr
        lon = transform.c + transform.a * (np.arange(shape[1]) + 0.5)
        lat = transform.f + transform.e * (np.arange(shape[0]) + 0.5)
        ds = xr.Dataset({"precip": (("time", "lat", "lon"), cube, {"units": "mm/day"})},
                        coords={"time": pd.to_datetime(days), "lat": lat, "lon": lon},
                        attrs={"source": "CHIRPS v2.0 daily p05 COG, UCSB CHC", "missing_days": len(days) - len(good)})
        ds.to_netcdf(out, encoding={"precip": {"zlib": True, "complevel": 4}})
        log.info("CHIRPS %d: %s, %d missing days, annual mean %.0f mm", year, shape,
                 len(days) - len(good), np.nanmean(np.nansum(cube, axis=0)))


def era5_points(bounds):
    w, s, e, n = bounds
    lats = np.round(np.arange(np.ceil(s * 10) / 10, n, 0.1), 2)
    lons = np.round(np.arange(np.ceil(w * 10) / 10, e, 0.1), 2)
    pts = pd.DataFrame([(la, lo) for la in lats for lo in lons], columns=["lat", "lon"])
    pts.insert(0, "point_id", [f"E{i:03d}" for i in range(len(pts))])
    return pts


def open_meteo(pts, start, end, model, variables):
    frames = []
    for i in range(0, len(pts), 25):                     # API accepts several points per call
        chunk = pts.iloc[i:i + 25]
        params = {"latitude": ",".join(map(str, chunk.lat)), "longitude": ",".join(map(str, chunk.lon)),
                  "start_date": start, "end_date": end, "daily": ",".join(variables),
                  "models": model, "timezone": "Asia/Dhaka"}
        for attempt in range(6):
            r = requests.get(OPEN_METEO, params=params, timeout=120, headers={"User-Agent": USER_AGENT})
            if r.status_code == 429:                     # free-tier rate limit: back off
                time.sleep(60 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        data = r.json()
        data = data if isinstance(data, list) else [data]
        for pid, d in zip(chunk.point_id, data):
            f = pd.DataFrame(d["daily"]).rename(columns={"time": "date"})
            f.insert(0, "point_id", pid)
            frames.append(f)
        time.sleep(1)
    return pd.concat(frames, ignore_index=True)


def era5(bounds, years):
    out_dir = BRONZE / "era5_land"
    out_dir.mkdir(parents=True, exist_ok=True)
    pts = era5_points(bounds)
    pts.to_csv(out_dir / "era5_points.csv", index=False)
    for year in years:
        out = out_dir / f"era5_daily_{year}.csv"
        if out.exists():
            log.info("cached: %s", out.name)
            continue
        start, end = f"{year}-01-01", f"{year}-12-31"
        land = open_meteo(pts, start, end, "era5_land", ERA5_LAND_VARS)
        atmos = open_meteo(pts, start, end, "era5", ERA5_VARS)
        df = land.merge(atmos, on=["point_id", "date"]).merge(pts, on="point_id")
        df.to_csv(out, index=False)
        log.info("ERA5 %d: %d points x %d days, null soil moisture %.1f%%", year, len(pts),
                 df.date.nunique(), 100 * df.soil_moisture_0_to_7cm_mean.isna().mean())


def main():
    bounds = study_bounds()
    start = dt.date.fromisoformat(str(CONFIG["periods"]["history_start"])).year
    end = dt.date.fromisoformat(str(CONFIG["periods"]["history_end"])).year
    years = range(start, end + 1)
    log.info("bounds %s, years %d-%d", [round(b, 3) for b in bounds], start, end)
    era5(bounds, years)
    chirps(bounds, years)


if __name__ == "__main__":
    main()
