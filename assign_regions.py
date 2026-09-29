"""Assign each offshore wind farm to its nearest coastal NUTS3 region.

Offshore assets sit in the sea and NUTS3 regions are land polygons, so the two
never overlap and a point-in-polygon join returns nothing. This script instead
does a nearest-neighbour join in a projected coordinate system, records the
distance, and drops anything beyond a stated cutoff rather than attributing a
far-offshore project to a coastline it has no relationship with.

Boundaries: Eurostat GISCO, NUTS 2021, 1:1 million scale, EPSG:3035.
Attribution required; free to reuse.

Usage
-----
    python assign_regions.py
    python assign_regions.py --max-distance-km 100
    python assign_regions.py --nuts-file data/raw/NUTS_RG_01M_2021_3035_LEVL_3.geojson
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

# GISCO file naming: theme_spatialtype_resolution_year_projection_subset.format
# RG is regions (polygons), 01M is the 1:1 million generalisation, 3035 is the
# European equal-area projection, LEVL_3 is NUTS3.
NUTS_URL = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_2021_3035_LEVL_3.geojson"
)

# EPSG:3035 is ETRS89 / LAEA Europe. Distances in this CRS are in metres and
# are area-true across the continent, which EPSG:4326 degrees are not.
WORKING_CRS = 3035

# Rough bounding box of European waters in degrees, used only to detect whether
# the longitude and latitude columns have been swapped upstream.
EUROPE_LON = (-32.0, 45.0)
EUROPE_LAT = (25.0, 75.0)

DEFAULT_MAX_DISTANCE_KM = 150.0

logger = logging.getLogger(__name__)


def fraction_inside_europe(longitude: pd.Series, latitude: pd.Series) -> float:
    """Share of points falling inside the European bounding box."""
    inside = (
        longitude.between(*EUROPE_LON) & latitude.between(*EUROPE_LAT)
    )
    return float(inside.mean())


def validate_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """Check longitude and latitude are the right way round, and swap if not.

    WKT writes POINT (longitude latitude) by convention, but not every export
    follows it, and a silent swap puts European assets in the Indian Ocean
    without raising any error. This compares how many points land inside
    Europe as given against how many would land inside Europe if swapped, and
    takes the better of the two.
    """
    as_given = fraction_inside_europe(df["longitude"], df["latitude"])
    if_swapped = fraction_inside_europe(df["latitude"], df["longitude"])

    if if_swapped > as_given:
        logger.warning(
            "Longitude and latitude look swapped (%.0f%% in Europe as given, "
            "%.0f%% swapped). Swapping.",
            as_given * 100,
            if_swapped * 100,
        )
        df = df.copy()
        df[["longitude", "latitude"]] = df[["latitude", "longitude"]].values
        as_given = if_swapped

    if as_given < 0.9:
        logger.warning(
            "Only %.0f%% of points fall inside the European bounding box. "
            "Check the source coordinates.",
            as_given * 100,
        )
    else:
        logger.info("%.0f%% of points fall inside the European bounding box", as_given * 100)

    return df


def load_wind_farms(path: Path) -> gpd.GeoDataFrame:
    """Read the cleaned wind farm table and build point geometry from it."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run prepare_windfarms.py first."
        )

    df = pd.read_csv(path)
    missing = {"longitude", "latitude"} - set(df.columns)
    if missing:
        raise KeyError(f"Expected longitude and latitude columns. Found: {list(df.columns)}")

    df = validate_coordinates(df)

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["longitude"], df["latitude"]),
        crs="EPSG:4326",
    )
    logger.info("Loaded %d wind farms", len(gdf))
    return gdf.to_crs(epsg=WORKING_CRS)


def load_nuts3(path: Path | None) -> gpd.GeoDataFrame:
    """Read the NUTS3 polygons from a local file, or fetch them from GISCO."""
    source = str(path) if path else NUTS_URL
    logger.info("Reading NUTS3 boundaries from %s", source)

    gdf = gpd.read_file(source)
    if gdf.empty:
        raise ValueError("The NUTS3 layer loaded with zero features.")

    rename = {"NUTS_ID": "nuts3_id", "NUTS_NAME": "nuts3_name", "CNTR_CODE": "country_code"}
    present = {old: new for old, new in rename.items() if old in gdf.columns}
    if "NUTS_ID" not in gdf.columns:
        raise KeyError(f"No NUTS_ID field in the boundary file. Found: {list(gdf.columns)}")
    gdf = gdf.rename(columns=present)

    keep = [column for column in ["nuts3_id", "nuts3_name", "country_code"] if column in gdf.columns]
    gdf = gdf[[*keep, gdf.geometry.name]]

    logger.info("Loaded %d NUTS3 regions", len(gdf))
    return gdf.to_crs(epsg=WORKING_CRS)


def assign(
    farms: gpd.GeoDataFrame, regions: gpd.GeoDataFrame, max_distance_km: float
) -> pd.DataFrame:
    """Join each farm to its nearest region within the distance cutoff.

    sjoin_nearest measures from each point to the closest edge of a polygon, so
    a farm 12 km off the coast of Cantabria gets 12 km, not the distance to the
    region's centre. Farms beyond the cutoff are kept in the output with no
    region attached, so that the number excluded can be reported rather than
    quietly lost.
    """
    max_distance_m = max_distance_km * 1000

    joined = gpd.sjoin_nearest(
        farms,
        regions,
        how="left",
        max_distance=max_distance_m,
        distance_col="distance_m",
    )

    # A point equidistant from two regions produces two rows. Keep the first.
    duplicates = joined.index.duplicated()
    if duplicates.any():
        logger.info("Dropped %d tied matches at equal distance", int(duplicates.sum()))
        joined = joined[~duplicates]

    joined["distance_to_region_km"] = (joined["distance_m"] / 1000).round(2)

    unassigned = int(joined["nuts3_id"].isna().sum())
    if unassigned:
        logger.info(
            "%d of %d farms lie beyond %.0f km from any region and have no assignment",
            unassigned,
            len(joined),
            max_distance_km,
        )

    drop = [column for column in ["index_right", "distance_m", "geometry"] if column in joined.columns]
    return pd.DataFrame(joined.drop(columns=drop))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/processed/wind_farms_clean.csv"),
        help="Cleaned wind farm CSV from prepare_windfarms.py.",
    )
    parser.add_argument(
        "--nuts-file",
        type=Path,
        default=None,
        help="Local NUTS3 boundary file. Omit to download from GISCO.",
    )
    parser.add_argument(
        "--max-distance-km",
        type=float,
        default=DEFAULT_MAX_DISTANCE_KM,
        help="Farms further than this from any region are left unassigned.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/wind_farms_with_region.csv"),
        help="Where to write the joined table.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        farms = load_wind_farms(args.input)
        regions = load_nuts3(args.nuts_file)
        result = assign(farms, regions, args.max_distance_km)
    except (ValueError, KeyError, FileNotFoundError) as error:
        logger.error("%s", error)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    logger.info("Wrote %d rows to %s", len(result), args.output)

    assigned = result[result["nuts3_id"].notna()]
    logger.info(
        "Assigned %d farms to %d distinct regions",
        len(assigned),
        assigned["nuts3_id"].nunique(),
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
