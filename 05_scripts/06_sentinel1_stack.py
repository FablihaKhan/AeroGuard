"""Step 6 - Sentinel-1 RTC backscatter stack for the settled unions.

Source: Microsoft Planetary Computer "sentinel-1-rtc" (gamma0, radiometrically terrain
corrected, 10 m, calibrated). This covers orbit correction, calibration and terrain
correction from the scope document; speckle is reduced by 2x2 averaging to 20 m.

One GeoTIFF per acquisition (date + relative orbit), frames of the same pass mosaicked:
  01_bronze_raw/sentinel1/s1rtc_{YYYYMMDD}_{orbit}{rel}.tif
  band 1 = VV, band 2 = VH, int16 dB x 100, nodata -32768, 20 m UTM 45N grid-aligned.
Index of all acquisitions: 01_bronze_raw/sentinel1/s1_acquisitions.csv

Usage:
  python 06_sentinel1_stack.py                          # full 2015-2025 archive (resumable)
  python 06_sentinel1_stack.py 2020-05-10 2020-06-15    # one window
"""

import sys
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

from common import BRONZE, CONFIG, CRS, CRS_GEO, SILVER, Frame, get_logger

log = get_logger("06_s1")
RES = 20
NODATA = -32768
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
OUT = BRONZE / "sentinel1"


def search(start, end, bbox):
    cat = pystac_client.Client.open(STAC, modifier=pc.sign_inplace)
    items = list(cat.search(collections=["sentinel-1-rtc"], bbox=bbox, datetime=f"{start}/{end}").items())
    rows = [{"date": i.datetime.date().isoformat(), "orbit": i.properties["sat:orbit_state"][0].upper(),
             "rel": i.properties["sat:relative_orbit"], "platform": i.properties["platform"].upper(),
             "item": i} for i in items]
    return pd.DataFrame(rows)


def mosaic(items, band, frame):
    k = 500 // RES
    shape = (frame.nrows * k, frame.ncols * k)
    transform = from_origin(frame.x0, frame.y0, RES, RES)
    out = np.full(shape, np.nan, dtype="float32")
    for item in items:
        tmp = np.full(shape, np.nan, dtype="float32")
        href = pc.sign(item.assets[band].href)
        with rasterio.open(href) as src:
            reproject(rasterio.band(src, 1), tmp, dst_transform=transform, dst_crs=CRS,
                      src_nodata=src.nodata if src.nodata is not None else 0, dst_nodata=np.nan,
                      resampling=Resampling.average)
        fill = np.isnan(out) & ~np.isnan(tmp)
        out[fill] = tmp[fill]
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10 * np.log10(out)
    db[~np.isfinite(db)] = np.nan
    return db, transform


def build(group, frame):
    (date, orbit, rel), g = group
    path = OUT / f"s1rtc_{date.replace('-', '')}_{orbit}{rel}.tif"
    if path.exists():
        return path, None
    try:
        bands = [mosaic(g["item"].tolist(), b, frame) for b in ("vv", "vh")]
    except Exception as e:  # transient HTTP / signing errors: skip, rerun later resumes
        log.warning("failed %s: %s", path.name, e)
        return path, None
    transform = bands[0][1]
    data = np.stack([np.where(np.isnan(b), NODATA, np.round(b * 100)).astype("int16") for b, _ in bands])
    valid = float(np.mean(data[0] != NODATA))
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[1], width=data.shape[2], count=2,
                       dtype="int16", crs=CRS, transform=transform, nodata=NODATA,
                       compress="deflate", predictor=2, tiled=True) as dst:
        dst.write(data)
        dst.set_band_description(1, "VV_dBx100")
        dst.set_band_description(2, "VH_dBx100")
    return path, valid


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else str(CONFIG["periods"]["history_start"])
    end = sys.argv[2] if len(sys.argv) > 2 else str(CONFIG["periods"]["history_end"])
    OUT.mkdir(parents=True, exist_ok=True)

    grid = gpd.read_file(SILVER / "grid" / "grid_500m_v01.gpkg")
    settled = grid[~grid.is_forest]
    frame = Frame(settled)
    bbox = list(settled.to_crs(CRS_GEO).total_bounds)
    log.info("frame %d x %d cells (%d x %d px at %d m)", frame.nrows, frame.ncols,
             frame.nrows * 25, frame.ncols * 25, RES)

    df = search(start, end, bbox)
    log.info("%d STAC items %s..%s", len(df), start, end)
    groups = list(df.groupby(["date", "orbit", "rel"]))
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda grp: build(grp, frame), groups))

    index = df.drop(columns="item").groupby(["date", "orbit", "rel"]).agg(
        platform=("platform", "first"), n_frames=("platform", "size")).reset_index()
    index["file"] = [p.name for p, _ in results]
    index["exists"] = [p.exists() for p, _ in results]
    new_valid = {p.name: v for p, v in results if v is not None}
    idx_path = OUT / "s1_acquisitions.csv"
    if idx_path.exists():
        old = pd.read_csv(idx_path)
        index = pd.concat([old, index]).drop_duplicates("file", keep="last")
        index["valid_frac"] = index["file"].map(new_valid).fillna(index.get("valid_frac"))
    else:
        index["valid_frac"] = index["file"].map(new_valid)
    index.sort_values("date").to_csv(idx_path, index=False)
    log.info("acquisitions written: %d new, %d total in index", len(new_valid), len(index))


if __name__ == "__main__":
    main()
