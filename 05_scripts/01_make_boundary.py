"""Step 1 - Clean study-area boundaries.

Outputs (02_silver_clean/boundary/):
  shyamnagar_upazila_v02.gpkg  - single upazila polygon (OSM relation 5295537), UTM 45N
  shyamnagar_unions_v01.gpkg   - union polygons (BBS/OCHA via geoBoundaries) clipped to the upazila,
                                 with is_forest flag for Sundarbans ranges
  gabura_pilot_v01.gpkg        - Gabura union polygon for the detailed pilot
  study_area_v01.gpkg          - dissolved land units (settled unions + Sundarbans range)

Note: the raw OSM export also holds relation 16500093, a different "Shyamnagar" in India
(80.7E). The v01 silver files kept it; it is dropped here by relation id.
"""

import geopandas as gpd

from common import BRONZE, CONFIG, CRS, SILVER, download, get_logger

log = get_logger("01_boundary")

ADM4_URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/"
            "gbOpen/BGD/ADM4/geoBoundaries-BGD-ADM4.geojson")
# A union belongs to the upazila when most of its area lies inside the OSM polygon.
MIN_OVERLAP = 0.5


def main():
    out_dir = SILVER / "boundary"
    relation = f"relation/{CONFIG['study_area']['upazila_osm_relation']}"

    raw = gpd.read_file(BRONZE / "boundary" / "shyamnagar_boundary_osm_raw_20260728.geojson")
    upazila = raw[raw["@id"] == relation].to_crs(CRS)
    assert len(upazila) == 1, f"expected one feature for {relation}, got {len(upazila)}"
    upazila = upazila[["@id", "name:en", "DIST_NAME", "geometry"]].rename(
        columns={"@id": "osm_id", "name:en": "name", "DIST_NAME": "district"})
    upazila["geometry"] = upazila.geometry.make_valid()
    upazila["area_km2"] = upazila.area / 1e6
    upazila.to_file(out_dir / "shyamnagar_upazila_v02.gpkg", layer="upazila", driver="GPKG")
    log.info("upazila: %.1f km2, bounds %s", upazila.area_km2.iloc[0], upazila.total_bounds.round(0))

    adm4 = gpd.read_file(download(ADM4_URL, BRONZE / "boundary" / "geoBoundaries-BGD-ADM4.geojson", log))
    adm4 = adm4.to_crs(CRS)
    adm4["geometry"] = adm4.geometry.make_valid()
    upa_geom = upazila.geometry.iloc[0]
    cand = adm4[adm4.intersects(upa_geom)].copy()
    cand["overlap"] = cand.intersection(upa_geom).area / cand.area
    unions = cand[cand["overlap"] >= MIN_OVERLAP].copy()
    unions["geometry"] = unions.intersection(upa_geom)
    unions = unions.rename(columns={"shapeName": "union_name", "shapeID": "union_id"})
    unions["is_forest"] = unions["union_name"].str.endswith("Range")
    unions["area_km2"] = unions.area / 1e6
    unions = unions[["union_id", "union_name", "is_forest", "overlap", "area_km2", "geometry"]]
    unions.to_file(out_dir / "shyamnagar_unions_v01.gpkg", layer="unions", driver="GPKG")
    log.info("unions kept (%d): %s", len(unions), ", ".join(sorted(unions.union_name)))
    log.info("unions dropped (overlap < %.1f): %s", MIN_OVERLAP,
             ", ".join(sorted(cand.loc[cand.overlap < MIN_OVERLAP, "shapeName"])))

    # The OSM upazila polygon runs ~1000 km2 into the Bay of Bengal (south to 21.4N),
    # so the study area is the union of the land units, not the OSM polygon.
    coverage = unions.area.sum() / upa_geom.area
    log.info("union coverage of upazila polygon: %.1f%% (remainder is sea)", 100 * coverage)
    study = gpd.GeoDataFrame(
        {"name": ["Shyamnagar study area"],
         "settled_km2": [unions.loc[~unions.is_forest, "area_km2"].sum()],
         "forest_km2": [unions.loc[unions.is_forest, "area_km2"].sum()]},
        geometry=[unions.union_all()], crs=CRS)
    study.to_file(out_dir / "study_area_v01.gpkg", layer="study_area", driver="GPKG")
    log.info("study area: settled %.1f km2 + forest %.1f km2",
             study.settled_km2.iloc[0], study.forest_km2.iloc[0])

    pilot = unions[unions.union_name == CONFIG["study_area"]["pilot_union"]]
    assert len(pilot) == 1
    pilot.to_file(out_dir / "gabura_pilot_v01.gpkg", layer="gabura", driver="GPKG")
    log.info("pilot %s: %.1f km2", pilot.union_name.iloc[0], pilot.area_km2.iloc[0])


if __name__ == "__main__":
    main()
