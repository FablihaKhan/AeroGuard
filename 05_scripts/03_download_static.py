"""Step 3 - Download static layers (no login needed), clipped to the study area.

Rasters are read straight from cloud-optimised GeoTIFFs over HTTP and only the study-area
window (+ buffer) is saved, in the source CRS and resolution, to 01_bronze_raw/.
Road network from OpenStreetMap through Overpass (osmnx); a Geofabrik Bangladesh extract
for facilities and water infrastructure (processed in 03b_osm_features.py).

Every file is appended to 00_documentation/download_manifest.csv with URL, date and sha256.
"""

import datetime as dt
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd
import rasterio
from rasterio.merge import merge

from common import BRONZE, CRS_GEO, DOCS, ROOT, SILVER, download, get_logger, sha256

log = get_logger("03_static")
BUFFER_M = 3000
TODAY = dt.date.today().isoformat()

COP_DEM = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM/"
           "Copernicus_DSM_COG_10_N{lat:02d}_00_E{lon:03d}_00_DEM.tif")
JRC = "https://storage.googleapis.com/global-surface-water/downloads2021/{layer}/{layer}_80E_30Nv1_4_2021.tif"
WORLDCOVER = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
              "ESA_WorldCover_10m_2021_v200_N21E087_Map.tif")
WORLDPOP = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/BGD/"
            "bgd_ppp_2020_constrained.tif")

def study_bounds_geo():
    study = gpd.read_file(SILVER / "boundary" / "study_area_v01.gpkg")
    return tuple(study.buffer(BUFFER_M).to_crs(CRS_GEO).total_bounds)


def record(rows, path: Path, source: str, url: str):
    rows.append({"file": str(path.relative_to(ROOT)), "source": source, "url": url,
                 "access_date": TODAY, "size_mb": round(path.stat().st_size / 1e6, 2),
                 "sha256": sha256(path)})


def clip_remote(urls, out: Path, bounds):
    """Merge one or more remote COGs (or local files) and save the window covering bounds (lon/lat)."""
    if out.exists():
        log.info("cached: %s", out.relative_to(ROOT))
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    srcs = [rasterio.open(u if Path(u).exists() else "/vsicurl/" + u) for u in urls]
    try:
        assert all(s.crs.to_epsg() == 4326 for s in srcs), "expected lon/lat source rasters"
        arr, transform = merge(srcs, bounds=bounds)
        profile = srcs[0].profile.copy()
        profile.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2], transform=transform,
                       compress="deflate", tiled=True, blockxsize=256, blockysize=256)
        profile.pop("photometric", None)
        with rasterio.open(out, "w", **profile) as dst:
            dst.write(arr)
    finally:
        for s in srcs:
            s.close()
    log.info("clipped: %s %s", out.relative_to(ROOT), arr.shape)
    return out


def main():
    bounds = study_bounds_geo()
    log.info("download bounds (lon/lat): %s", [round(b, 4) for b in bounds])
    rows = []

    tiles = [(lat, lon) for lat in (21, 22) for lon in (88, 89)]
    dem_urls = [COP_DEM.format(lat=a, lon=o) for a, o in tiles]
    p = clip_remote(dem_urls, BRONZE / "dem" / "copernicus_glo30_dsm_shyamnagar.tif", bounds)
    record(rows, p, "Copernicus DEM GLO-30 (DSM, 2021 release)", "; ".join(dem_urls))

    for layer in ("occurrence", "seasonality", "recurrence", "transitions"):
        url = JRC.format(layer=layer)
        p = clip_remote([url], BRONZE / "jrc_water" / f"jrc_gsw_{layer}_v1_4_2021_shyamnagar.tif", bounds)
        record(rows, p, f"JRC Global Surface Water v1.4 - {layer}", url)

    p = clip_remote([WORLDCOVER], BRONZE / "landcover" / "esa_worldcover_2021_v200_shyamnagar.tif", bounds)
    record(rows, p, "ESA WorldCover 10 m 2021 v200", WORLDCOVER)

    # data.worldpop.org does not serve byte ranges, so fetch the national file first.
    full = download(WORLDPOP, BRONZE / "population" / "_national" / "bgd_ppp_2020_constrained.tif", log)
    p = clip_remote([str(full)], BRONZE / "population" / "worldpop_bgd_ppp_2020_constrained_shyamnagar.tif", bounds)
    record(rows, p, "WorldPop 2020 constrained (100 m, people per pixel)", WORLDPOP)

    # --- OpenStreetMap -----------------------------------------------------------------
    ox.settings.use_cache = True
    ox.settings.requests_timeout = 240
    ox.settings.cache_folder = str(BRONZE / "osm" / "_overpass_cache")
    west, south, east, north = bounds
    osm_dir = BRONZE / "osm"

    roads_path = osm_dir / f"osm_roads_shyamnagar_{TODAY.replace('-', '')}.gpkg"
    if not roads_path.exists():
        g = ox.graph_from_bbox((west, south, east, north), network_type="all", simplify=True,
                               retain_all=True, truncate_by_edge=True)
        nodes, edges = ox.graph_to_gdfs(g)
        for df in (nodes, edges):
            for col in df.columns.drop("geometry"):
                if df[col].map(type).eq(list).any():
                    df[col] = df[col].astype(str)
        nodes.to_file(roads_path, layer="nodes", driver="GPKG")
        edges.to_file(roads_path, layer="edges", driver="GPKG")
        log.info("osm roads: %d nodes, %d edges", len(nodes), len(edges))
    record(rows, roads_path, "OpenStreetMap road network (osmnx, network_type=all)", "overpass-api.de")

    # Facilities and water infrastructure: Overpass times out for these tag sets, so they are
    # read from a dated Geofabrik extract in 03b_osm_features.py instead.
    pbf = download("https://download.geofabrik.de/asia/bangladesh-latest.osm.pbf",
                   osm_dir / "_national" / f"bangladesh-{TODAY.replace('-', '')}.osm.pbf", log)
    record(rows, pbf, "OpenStreetMap Bangladesh extract (Geofabrik)",
           "https://download.geofabrik.de/asia/bangladesh-latest.osm.pbf")

    manifest = DOCS / "download_manifest.csv"
    new = pd.DataFrame(rows)
    if manifest.exists():
        new = pd.concat([pd.read_csv(manifest), new]).drop_duplicates("file", keep="last")
    new.to_csv(manifest, index=False)
    log.info("manifest: %d files", len(new))


if __name__ == "__main__":
    main()
