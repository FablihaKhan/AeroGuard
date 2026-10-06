"""Step 3b - Facilities and water infrastructure from the Geofabrik Bangladesh OSM extract.

Overpass queries for these tags time out on every public server, so this step reads a dated
Geofabrik snapshot (01_bronze_raw/osm/_national/bangladesh-YYYYMMDD.osm.pbf) with GDAL's
OSM driver, keeps features inside the study bounds and classifies them.

Outputs (02_silver_clean/, UTM 45N):
  facilities/facilities_osm_v01.gpkg  - point per facility: category = health | shelter | school |
                                        market | admin | worship | other (polygons -> centroids)
  water/water_infra_osm_v01.gpkg      - layers: waterways (lines), embankments (lines),
                                        sluices (points)
"""

import re

import geopandas as gpd
import pandas as pd
import pyogrio

from common import BRONZE, CRS, CRS_GEO, SILVER, get_logger

log = get_logger("03b_osm")
BUFFER_M = 3000

HEALTH = {"hospital", "clinic", "doctors", "health_post", "healthcare", "pharmacy"}
SHELTER_WORDS = re.compile(r"cyclone|shelter|আশ্রয়", re.I)
TAG_RE = re.compile(r'"([^"]+)"=>"((?:[^"\\]|\\.)*)"')


def parse_tags(s):
    return dict(TAG_RE.findall(s)) if isinstance(s, str) else {}


def read_layer(pbf, layer, bbox):
    df = pyogrio.read_dataframe(pbf, layer=layer, bbox=bbox)
    tags = df["other_tags"].map(parse_tags)
    for key in ("amenity", "healthcare", "emergency", "building", "waterway", "man_made",
                "embankment", "name:en", "flood_prone", "material"):
        if key not in df.columns:
            df[key] = tags.map(lambda t, k=key: t.get(k))
    return df


def categorise(row):
    # missing tags are NaN, which is truthy - normalise to None first
    row = {k: (v if isinstance(v, str) and v else None) for k, v in row.items()}
    name = " ".join(row.get(c) or "" for c in ("name", "name:en"))
    amenity, building = row.get("amenity"), row.get("building")
    if amenity in HEALTH or row.get("healthcare") or building == "hospital":
        return "health"
    if (amenity == "shelter" or row.get("emergency") in ("assembly_point", "disaster_shelter")
            or building == "shelter" or SHELTER_WORDS.search(name)):
        return "shelter"
    if amenity in ("school", "college", "university") or building == "school":
        return "school"          # schools commonly double as cyclone shelters
    if amenity == "marketplace":
        return "market"
    if amenity in ("townhall", "police", "community_centre"):
        return "admin"
    if amenity == "place_of_worship":
        return "worship"
    return None


def main():
    pbf = sorted((BRONZE / "osm" / "_national").glob("bangladesh-*.osm.pbf"))[-1]
    log.info("reading %s", pbf.name)
    study = gpd.read_file(SILVER / "boundary" / "study_area_v01.gpkg")
    bbox = tuple(study.buffer(BUFFER_M).to_crs(CRS_GEO).total_bounds)

    pts = read_layer(pbf, "points", bbox)
    polys = read_layer(pbf, "multipolygons", bbox)
    lines = read_layer(pbf, "lines", bbox)
    log.info("in bbox: %d points, %d lines, %d polygons", len(pts), len(lines), len(polys))

    # --- facilities ---------------------------------------------------------------------
    polys_c = polys.copy()
    polys_c["geometry"] = polys_c.to_crs(CRS).centroid.to_crs(CRS_GEO)
    fac = pd.concat([pts.assign(osm_type="node", osm_id=pts["osm_id"]),
                     polys_c.assign(osm_type="area", osm_id=polys_c["osm_id"].fillna(polys_c["osm_way_id"]))],
                    ignore_index=True)
    fac["category"] = [categorise(r) for r in fac.drop(columns="geometry").to_dict("records")]
    fac = fac[fac.category.notna()]
    fac = gpd.GeoDataFrame(fac[["osm_type", "osm_id", "name", "name:en", "category", "amenity",
                                "healthcare", "building", "geometry"]], geometry="geometry", crs=CRS_GEO).to_crs(CRS)
    out = SILVER / "facilities" / "facilities_osm_v01.gpkg"
    fac.to_file(out, layer="facilities", driver="GPKG")
    log.info("facilities: %s", fac.category.value_counts().to_dict())

    # --- water infrastructure -----------------------------------------------------------
    lines = lines.to_crs(CRS)
    waterways = lines[lines["waterway"].isin(["river", "canal", "stream", "drain", "ditch", "tidal_channel"])]
    embank = lines[lines["man_made"].isin(["dyke", "embankment"]) | lines["embankment"].eq("yes")]
    sluice_pts = pts[pts["waterway"].isin(["sluice_gate", "lock_gate", "weir", "dam"])
                     | pts["man_made"].eq("sluice_gate")].to_crs(CRS)
    wout = SILVER / "water" / "water_infra_osm_v01.gpkg"
    keep = ["osm_id", "name", "waterway", "man_made", "embankment", "geometry"]
    for name, df in (("waterways", waterways), ("embankments", embank), ("sluices", sluice_pts)):
        cols = [c for c in keep if c in df.columns]
        if len(df):
            df[cols].to_file(wout, layer=name, driver="GPKG")
        log.info("%s: %d features%s", name, len(df),
                 f", {df.length.sum() / 1000:.0f} km" if name != "sluices" and len(df) else "")
    log.info("waterway types: %s", waterways.waterway.value_counts().to_dict())


if __name__ == "__main__":
    main()
