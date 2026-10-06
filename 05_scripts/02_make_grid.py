"""Step 2 - Build the 500 m analysis grid.

Output: 02_silver_clean/grid/grid_500m_v01.gpkg (layer "grid"), one row per cell:
  grid_id, row, col, union_name, is_forest, is_gabura, land_fraction, cx, cy, lon, lat

The grid is aligned to multiples of the cell size in UTM 45N so that rasters resampled
to 500 m later line up exactly with the cells.
"""

import geopandas as gpd
import numpy as np
from shapely.geometry import box

from common import CONFIG, CRS_GEO, SILVER, get_logger

log = get_logger("02_grid")


def main():
    size = CONFIG["grid"]["cell_size_m"]
    bdir = SILVER / "boundary"
    study = gpd.read_file(bdir / "study_area_v01.gpkg")
    unions = gpd.read_file(bdir / "shyamnagar_unions_v01.gpkg")
    area_geom = study.geometry.iloc[0]

    xmin, ymin, xmax, ymax = study.total_bounds
    xmin, ymin = np.floor(xmin / size) * size, np.floor(ymin / size) * size
    xmax, ymax = np.ceil(xmax / size) * size, np.ceil(ymax / size) * size
    xs = np.arange(xmin, xmax, size)
    ys = np.arange(ymax, ymin, -size)          # row 0 at the north edge, like a raster

    cells, rows, cols = [], [], []
    for r, y in enumerate(ys):
        for c, x in enumerate(xs):
            cells.append(box(x, y - size, x + size, y))
            rows.append(r)
            cols.append(c)
    grid = gpd.GeoDataFrame({"row": rows, "col": cols}, geometry=cells, crs=study.crs)
    grid = grid[grid.intersects(area_geom)].copy()
    grid["land_fraction"] = grid.intersection(area_geom).area / size**2
    grid = grid[grid.land_fraction >= CONFIG["grid"]["min_land_fraction"]].copy()

    # Assign each cell to the union holding the largest share of it.
    parts = gpd.overlay(grid.reset_index(names="cell")[["cell", "geometry"]],
                        unions[["union_name", "is_forest", "geometry"]], how="intersection")
    parts["a"] = parts.area
    best = parts.sort_values("a").drop_duplicates("cell", keep="last").set_index("cell")
    grid["union_name"] = best["union_name"]
    grid["is_forest"] = best["is_forest"].astype(bool)
    grid["is_gabura"] = grid["union_name"] == CONFIG["study_area"]["pilot_union"]

    grid = grid.sort_values(["row", "col"]).reset_index(drop=True)
    grid.insert(0, "grid_id", [f"{CONFIG['grid']['id_prefix']}_{i:05d}" for i in range(len(grid))])
    cent = grid.geometry.centroid
    grid["cx"], grid["cy"] = cent.x, cent.y
    cent_geo = cent.to_crs(CRS_GEO)
    grid["lon"], grid["lat"] = cent_geo.x.round(6), cent_geo.y.round(6)

    out = SILVER / "grid" / "grid_500m_v01.gpkg"
    grid.to_file(out, layer="grid", driver="GPKG")
    grid.drop(columns="geometry").to_csv(SILVER / "grid" / "grid_500m_v01_attributes.csv", index=False)

    log.info("grid cells: %d total | settled %d | forest %d | Gabura %d",
             len(grid), (~grid.is_forest).sum(), grid.is_forest.sum(), grid.is_gabura.sum())
    log.info("cells per union:\n%s", grid.union_name.value_counts().to_string())
    log.info("wrote %s", out.name)


if __name__ == "__main__":
    main()
