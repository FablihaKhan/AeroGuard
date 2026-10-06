"""Step 4 - Static (time-invariant) features for every 500 m grid cell.

Each raster is warped (common.Frame) onto a UTM sub-grid whose pixels nest exactly inside the 500 m cells
(25 m for DEM/JRC, 10 m for WorldCover, 100 m for population), then block-aggregated.

Output: 03_gold_analysis_ready/static_features/static_features_v01.csv (one row per grid_id)
        02_silver_clean/dem/dem_utm25m.tif and water/permanent_water_utm25m.tif (QA layers)
"""

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.warp import Resampling
from scipy import ndimage

from common import BRONZE, CELL, CRS, GOLD, SILVER, Frame, get_logger

log = get_logger("04_static")

WORLDCOVER_CLASSES = {10: "tree", 20: "shrub", 30: "grass", 40: "cropland", 50: "built",
                      60: "bare", 80: "water", 90: "wetland", 95: "mangrove"}
# JRC occurrence >= 75 % of observed months = permanent (river / large channel) water
PERMANENT_OCCURRENCE = 75


def save_qa(arr, transform, path, nodata=np.nan):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype=arr.dtype, crs=CRS, transform=transform, nodata=nodata,
                       compress="deflate", tiled=True) as dst:
        dst.write(arr, 1)


def dem_features(frame, feats):
    res = 25
    dem, transform = frame.warp(BRONZE / "dem" / "copernicus_glo30_dsm_shyamnagar.tif",
                                res, Resampling.bilinear)
    save_qa(dem, transform, SILVER / "dem" / "dem_utm25m.tif")
    b = frame.blocks(dem, res)
    feats["elev_mean"] = np.nanmean(b, axis=1)
    feats["elev_min"] = np.nanmin(b, axis=1)
    feats["elev_p10"] = np.nanpercentile(b, 10, axis=1)
    feats["elev_p50"] = np.nanmedian(b, axis=1)
    feats["elev_std"] = np.nanstd(b, axis=1)

    gy, gx = np.gradient(np.nan_to_num(dem, nan=np.nanmean(dem)), res)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    feats["slope_mean_deg"] = np.nanmean(frame.blocks(slope, res), axis=1)

    # Topographic position: cell elevation relative to its surroundings. Negative = basin.
    cell_elev = np.full((frame.nrows, frame.ncols), np.nan)
    cell_elev[frame.rows, frame.cols] = feats["elev_p50"]
    for radius_km in (1.5, 5):
        size = int(2 * radius_km * 1000 / CELL) + 1
        valid = ~np.isnan(cell_elev)
        s = ndimage.uniform_filter(np.where(valid, cell_elev, 0), size)
        n = ndimage.uniform_filter(valid.astype(float), size)
        local = s / np.maximum(n, 1e-9)
        feats[f"tpi_{str(radius_km).replace('.', 'p')}km"] = (cell_elev - local)[frame.rows, frame.cols]

    # Depression depth: how much a cell sits below the minimum "rim" of its 3x3 neighbourhood
    # max-filter. Proxy for local ponding potential at 500 m.
    filled = ndimage.grey_closing(np.nan_to_num(cell_elev, nan=np.nanmax(cell_elev)), size=(3, 3))
    feats["depression_depth_m"] = (filled - cell_elev)[frame.rows, frame.cols].clip(min=0)


def jrc_features(frame, feats):
    res = 25
    occ, transform = frame.warp(BRONZE / "jrc_water" / "jrc_gsw_occurrence_v1_4_2021_shyamnagar.tif",
                                res, Resampling.nearest, dtype="float32", nodata=255)
    occ[occ == 255] = np.nan
    occ[occ > 100] = np.nan
    permanent = (occ >= PERMANENT_OCCURRENCE).astype("uint8")
    save_qa(permanent, transform, SILVER / "water" / "permanent_water_utm25m.tif", nodata=255)

    b = frame.blocks(occ, res)
    feats["perm_water_frac"] = np.nanmean(b >= PERMANENT_OCCURRENCE, axis=1)
    feats["seasonal_water_frac"] = np.nanmean((b > 0) & (b < PERMANENT_OCCURRENCE), axis=1)
    land = np.where(b < PERMANENT_OCCURRENCE, b, np.nan)
    with np.errstate(all="ignore"):
        feats["jrc_occurrence_land_mean"] = np.nanmean(land, axis=1)

    # Distance from cell centre to nearest permanent water pixel (rivers / khals).
    dist = ndimage.distance_transform_edt(permanent == 0) * res
    centre = (CELL // res) // 2
    feats["dist_perm_water_m"] = frame.blocks(dist, res)[:, centre * (CELL // res) + centre]


def landcover_features(frame, feats):
    res = 10
    lc, _ = frame.warp(BRONZE / "landcover" / "esa_worldcover_2021_v200_shyamnagar.tif",
                       res, Resampling.nearest, dtype="uint8", nodata=0)
    b = frame.blocks(lc, res)
    valid = (b > 0).sum(axis=1)
    for code, name in WORLDCOVER_CLASSES.items():
        feats[f"lc_{name}_frac"] = (b == code).sum(axis=1) / np.maximum(valid, 1)


def population_features(frame, feats):
    res = 100
    # People per pixel is a count, so warp with sum to keep totals.
    pop, _ = frame.warp(BRONZE / "population" / "worldpop_bgd_ppp_2020_constrained_shyamnagar.tif",
                        res, Resampling.sum)
    b = frame.blocks(pop, res)
    feats["population_2020"] = np.nansum(np.where(b < 0, 0, b), axis=1)
    src_total = rasterio.open(BRONZE / "population" / "worldpop_bgd_ppp_2020_constrained_shyamnagar.tif").read(1)
    log.info("population: grid total %.0f (clipped source raster total %.0f, includes buffer)",
             feats["population_2020"].sum(), src_total[src_total > 0].sum())


def road_features(grid, feats):
    roads = list((BRONZE / "osm").glob("osm_roads_shyamnagar_*.gpkg"))
    edges = gpd.read_file(sorted(roads)[-1], layer="edges").to_crs(CRS)
    parts = gpd.overlay(edges[["highway", "geometry"]], grid[["grid_id", "geometry"]],
                        how="intersection", keep_geom_type=True)
    parts["len_km"] = parts.length / 1000
    major = parts.highway.str.contains("primary|secondary|tertiary|trunk", na=False)
    feats["road_km"] = feats.grid_id.map(parts.groupby("grid_id").len_km.sum()).fillna(0)
    feats["road_major_km"] = feats.grid_id.map(parts[major].groupby("grid_id").len_km.sum()).fillna(0)


def main():
    grid = gpd.read_file(SILVER / "grid" / "grid_500m_v01.gpkg")
    frame = Frame(grid)
    feats = grid[["grid_id", "union_name", "is_forest", "is_gabura", "land_fraction", "lon", "lat"]].copy()

    for step in (dem_features, jrc_features, landcover_features, population_features):
        step(frame, feats)
        log.info("done: %s", step.__name__)
    road_features(grid, feats)
    log.info("done: road_features")

    out = GOLD / "static_features" / "static_features_v01.csv"
    feats.round(4).to_csv(out, index=False)
    log.info("wrote %s: %d cells x %d features", out.name, len(feats), feats.shape[1] - 1)
    settled = feats[~feats.is_forest]
    log.info("settled-cell summary:\n%s", settled.describe().T[["mean", "min", "50%", "max"]].round(2).to_string())


if __name__ == "__main__":
    main()
