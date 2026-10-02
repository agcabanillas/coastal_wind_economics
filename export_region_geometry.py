"""Export the polygons of the analysed regions for mapping.

The ranked output is a table keyed on NUTS3 code, which a visualisation tool
cannot map on its own because NUTS codes are not a geography any of them
recognise. This writes the matching polygons as a GeoJSON file that can be
joined back to the ranked table on the region code, which is what turns the
ranking into a choropleth.

Geometry is written in EPSG:4326, since web mapping tools expect longitude and
latitude even when the analysis was done in a projected CRS.

Usage
-----
    python export_region_geometry.py
    python export_region_geometry.py --nuts-file data/raw/NUTS_RG_01M_2024_3035_LEVL_3.geojson
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

# Defined here rather than imported, so this script stands on its own.
NUTS_URL_TEMPLATE = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
    "NUTS_RG_01M_{year}_3035_LEVL_3.geojson"
)

# Must match the vintage assign_regions.py used, or region codes revised
# between classification versions will have no polygon and drop off the map.
DEFAULT_NUTS_YEAR = 2021

logger = logging.getLogger(__name__)


def load_exposure(path: Path) -> pd.DataFrame:
    """Read the ranked table that the map will be joined to."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run load_database.py first.")

    df = pd.read_csv(path)
    if "nuts3_id" not in df.columns:
        raise KeyError(f"No nuts3_id column. Found: {list(df.columns)}")

    logger.info("Exposure table: %d regions", len(df))
    return df


def load_nuts3(path: Path | None, year: int) -> gpd.GeoDataFrame:
    """Read the NUTS3 polygons from a local file or from GISCO."""
    source = str(path) if path else NUTS_URL_TEMPLATE.format(year=year)
    logger.info("Reading NUTS3 boundaries from %s", source)

    gdf = gpd.read_file(source)
    if "NUTS_ID" not in gdf.columns:
        raise KeyError(f"No NUTS_ID field. Found: {list(gdf.columns)}")

    return gdf.rename(columns={"NUTS_ID": "nuts3_id", "NUTS_NAME": "nuts3_name"})


def build_map_layer(regions: gpd.GeoDataFrame, exposure: pd.DataFrame) -> gpd.GeoDataFrame:
    """Keep the analysed regions and attach their exposure values.

    The values travel with the geometry so that the map works as a single
    layer, without needing a join to be configured in the visualisation tool.
    Any region in the ranked table with no matching polygon is reported rather
    than dropped silently.
    """
    wanted = set(exposure["nuts3_id"])
    matched = regions[regions["nuts3_id"].isin(wanted)].copy()

    missing = wanted - set(matched["nuts3_id"])
    if missing:
        logger.warning(
            "%d regions in the exposure table have no polygon: %s",
            len(missing),
            sorted(missing)[:10],
        )

    value_columns = [column for column in exposure.columns if column != "nuts3_name"]
    layer = matched.merge(exposure[value_columns], on="nuts3_id", how="left")

    keep = [
        column
        for column in [
            "nuts3_id", "nuts3_name", "country_code", "project_count", "total_mw",
            "pipeline_mw", "installed_mw", "gdp_meur", "population",
            "mw_per_bn_eur_gdp", "rank_in_country",
        ]
        if column in layer.columns
    ]

    layer = layer[[*keep, layer.geometry.name]]
    logger.info("Map layer: %d regions with geometry", len(layer))
    return layer.to_crs(epsg=4326)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--exposure",
        type=Path,
        default=Path("data/processed/regional_exposure.csv"),
        help="Ranked table exported by load_database.py.",
    )
    parser.add_argument("--nuts-file", type=Path, default=None)
    parser.add_argument("--nuts-year", type=int, default=DEFAULT_NUTS_YEAR)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/regional_exposure_map.geojson"),
        help="Where to write the mappable layer.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        exposure = load_exposure(args.exposure)
        regions = load_nuts3(args.nuts_file, args.nuts_year)
        layer = build_map_layer(regions, exposure)
    except (FileNotFoundError, KeyError, ValueError) as error:
        logger.error("%s", error)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    layer.to_file(args.output, driver="GeoJSON")
    logger.info("Wrote %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
