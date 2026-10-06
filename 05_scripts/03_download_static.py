"""Step 3 - Download static layers (no login needed), clipped to the study area.

Rasters are read straight from cloud-optimised GeoTIFFs over HTTP and only the study-area
window (+ buffer) is saved, in the source CRS and resolution, to 01_bronze_raw/.
Vector data come from OpenStreetMap through Overpass (osmnx).

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

OSM_TAGS = {
    "facilities": {
        "amenity": ["hospital", "clinic", "doctors", "health_post", "pharmacy", "shelter",
                    "school", "college", "place_of_worship", "marketplace", "police", "townhall"],
        "healthcare": True,
        "emergency": ["assembly_point", "disaster_shelter"],
        "building": ["hospital", "school", "shelter"],
    },
    "water_infra": {
        "waterway": ["river", "canal", "stream", "drain", "ditch", "sluice_gate", "weir", "dam", "lock_gate"],
        "man_made": ["dyke", "embankment", "sluice_gate"],
        "embankment": True,
        "natural": ["water", "coastline"],
    },
}


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


OVERPASS_MIRRORS = ["https://overpass-api.de/api", "https://overpass.kumi.systems/api",
                    "https://overpass.private.coffee/api"]


def osm_features(bbox, tags):
    """features_from_bbox with fallback across Overpass mirrors (the main server often times out)."""
    for url in OVERPASS_MIRRORS:
        ox.settings.overpass_url = url
        try:
            return ox.features_from_bbox(bbox, tags).reset_index()
        except Exception as e:  # network errors from requests/osmnx
            log.warning("overpass %s failed: %s", url, type(e).__name__)
    raise RuntimeError("all Overpass mirrors failed")


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
    ox.settings.requests_timeout = 60
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

    for name, tags in OSM_TAGS.items():
        path = osm_dir / f"osm_{name}_shyamnagar_{TODAY.replace('-', '')}.gpkg"
        if not path.exists():
            feats = osm_features((west, south, east, north), tags)
            for col in feats.columns:
                if col != "geometry" and feats[col].dtype == object:
                    feats[col] = feats[col].astype(str).replace("nan", None)
            for gtype, sub in feats.groupby(feats.geometry.geom_type):
                sub.to_file(path, layer=gtype.lower(), driver="GPKG")
            log.info("osm %s: %d features %s", name, len(feats),
                     feats.geometry.geom_type.value_counts().to_dict())
        record(rows, path, f"OpenStreetMap {name}", "overpass-api.de")

    manifest = DOCS / "download_manifest.csv"
    new = pd.DataFrame(rows)
    if manifest.exists():
        new = pd.concat([pd.read_csv(manifest), new]).drop_duplicates("file", keep="last")
    new.to_csv(manifest, index=False)
    log.info("manifest: %d files", len(new))


if __name__ == "__main__":
    main()
