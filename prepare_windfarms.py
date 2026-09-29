"""Prepare the EMODnet offshore wind farm dataset for visualisation.

Reads the EMODnet Human Activities wind farm point layer, either from a local
spatial file or directly from the EMODnet WFS endpoint, harmonises the status
field, derives a few analysis columns and writes a flat CSV ready for Tableau,
Power BI or any other visualisation tool.

Data source: EMODnet Human Activities, Energy, Wind Farms (originator CETMAR).
Licence: CC-BY 4.0.

Usage
-----
    python prepare_windfarms.py --input data/raw/windfarms_points.shp
    python prepare_windfarms.py --wfs
    python prepare_windfarms.py --wfs --output data/processed/wind_farms.csv --summary
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

import pandas as pd

WFS_URL = (
    "https://ows.emodnet-humanactivities.eu/wfs"
    "?service=WFS&version=1.1.0&request=GetFeature"
    "&typeName=emodnet:windfarms&outputFormat=application/json"
)

# Shapefile field names are truncated to ten characters, so the same column
# arrives under different names depending on the format the data came in.
COLUMN_ALIASES: dict[str, str] = {
    "n_turbine": "n_turbines",
    "dist_coas": "dist_coast",
    "area_sqk": "area_sqkm",
    "start": "year",
    "startyear": "year",
}

# EMODnet uses a handful of status labels that vary slightly between releases.
STATUS_GROUPS: dict[str, str] = {
    "production": "In production",
    "operational": "In production",
    "construction": "Under construction",
    "under construction": "Under construction",
    "approved": "Approved",
    "authorised": "Approved",
    "planned": "Planned",
    "dismantled": "Other",
    "test site": "Other",
}

STATUS_ORDER: list[str] = [
    "In production",
    "Under construction",
    "Approved",
    "Planned",
    "Other",
    "Unknown",
]

KEEP_COLUMNS: list[str] = [
    "name",
    "country",
    "status",
    "stage",
    "year",
    "power_mw",
    "n_turbines",
    "mw_per_turbine",
    "dist_coast_km",
    "longitude",
    "latitude",
]

logger = logging.getLogger(__name__)


COORDINATE_ALIASES: dict[str, list[str]] = {
    "longitude": ["longitude", "lon", "long", "x", "xcoord", "x_coord"],
    "latitude": ["latitude", "lat", "y", "ycoord", "y_coord"],
}

WKT_COLUMNS: list[str] = ["the_geom", "geometry", "wkt", "geom"]


def _coordinates_from_wkt(series: pd.Series) -> pd.DataFrame:
    """Pull longitude and latitude out of a WKT POINT column."""
    pattern = r"POINT\s*\(\s*(-?\d+\.?\d*)\s+(-?\d+\.?\d*)"
    extracted = series.astype("string").str.extract(pattern, flags=re.IGNORECASE)
    return pd.DataFrame(
        {
            "longitude": pd.to_numeric(extracted[0], errors="coerce"),
            "latitude": pd.to_numeric(extracted[1], errors="coerce"),
        },
        index=series.index,
    )


def resolve_coordinates(df: pd.DataFrame) -> pd.DataFrame:
    """Add longitude and latitude columns to a flat table.

    Handles the three shapes the EMODnet CSV export arrives in: separate
    coordinate columns under any of the usual names, a WKT geometry column, or
    neither, in which case the caller is told what was actually found.
    """
    lowered = {column.lower(): column for column in df.columns}

    found: dict[str, str] = {}
    for target, candidates in COORDINATE_ALIASES.items():
        for candidate in candidates:
            if candidate in lowered:
                found[target] = lowered[candidate]
                break

    if len(found) == 2:
        logger.info("Using coordinate columns %s", found)
        df = df.copy()
        df["longitude"] = pd.to_numeric(df[found["longitude"]], errors="coerce")
        df["latitude"] = pd.to_numeric(df[found["latitude"]], errors="coerce")
        return df

    for candidate in WKT_COLUMNS:
        if candidate in lowered:
            logger.info("Parsing coordinates from WKT column '%s'", lowered[candidate])
            df = df.copy()
            return df.join(_coordinates_from_wkt(df[lowered[candidate]]))

    raise KeyError(
        "No coordinate columns found. Looked for "
        f"{sorted(sum(COORDINATE_ALIASES.values(), []))} and {WKT_COLUMNS}. "
        f"The file has: {sorted(df.columns)}"
    )


def load_windfarms(input_path: Path | None, use_wfs: bool) -> pd.DataFrame:
    """Load the wind farm point layer as a flat table with coordinates.

    Accepts a CSV export, any spatial file geopandas can read (.shp, .gpkg,
    .geojson), or a direct fetch from the EMODnet WFS endpoint. Only the last
    two need geopandas installed, so a CSV run works with pandas alone.

    Parameters
    ----------
    input_path
        Path to a local file. Ignored if ``use_wfs`` is True.
    use_wfs
        Fetch the layer from the EMODnet WFS endpoint instead of a local file.

    Returns
    -------
    pandas.DataFrame
        The raw attributes plus longitude and latitude in EPSG:4326.
    """
    if use_wfs:
        logger.info("Reading wind farm points from the EMODnet WFS endpoint")
        return _from_spatial(WFS_URL)

    if input_path is None:
        raise ValueError("Provide --input with a file path, or pass --wfs.")
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    logger.info("Reading wind farm points from %s", input_path)

    if input_path.suffix.lower() == ".csv":
        df = pd.read_csv(input_path)
        if df.empty:
            raise ValueError("The CSV loaded with zero rows.")
        return resolve_coordinates(df)

    return _from_spatial(str(input_path))


def _from_spatial(source: str) -> pd.DataFrame:
    """Read a spatial source with geopandas and flatten it to a table."""
    try:
        import geopandas as gpd
    except ImportError as error:
        raise ImportError(
            "geopandas is needed to read spatial files or the WFS endpoint. "
            "Install it, or download the CSV export and pass it with --input."
        ) from error

    gdf = gpd.read_file(source)
    if gdf.empty:
        raise ValueError("The wind farm layer loaded with zero features.")
    if gdf.crs is None:
        raise ValueError("The layer has no CRS defined, so it cannot be reprojected.")

    gdf = gdf.to_crs(epsg=4326)
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    df["longitude"] = gdf.geometry.x
    df["latitude"] = gdf.geometry.y
    return df


def harmonise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase column names and map known shapefile truncations back."""
    df = df.rename(columns=str.lower)
    aliases = {old: new for old, new in COLUMN_ALIASES.items() if old in df.columns}
    if aliases:
        logger.info("Renaming truncated fields: %s", aliases)
    return df.rename(columns=aliases)


def group_status(raw_status: pd.Series) -> pd.Series:
    """Collapse the EMODnet status labels into a small ordered set."""
    grouped = raw_status.astype("string").str.strip().str.lower().map(STATUS_GROUPS)
    unmatched = raw_status[grouped.isna() & raw_status.notna()].unique()
    if len(unmatched) > 0:
        logger.warning("Status values not in STATUS_GROUPS: %s", list(unmatched))
    return pd.Categorical(
        grouped.fillna("Unknown"), categories=STATUS_ORDER, ordered=True
    )


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Derive the analysis columns and return a flat table.

    Drops records with no usable capacity, since a wind farm of unknown size
    cannot contribute to any of the capacity views.
    """
    df = harmonise_columns(df)

    missing = {"country", "status", "power_mw"} - set(df.columns)
    if missing:
        raise KeyError(f"Expected columns missing from the layer: {sorted(df.columns)}")

    df = df.copy()
    df["status"] = group_status(df["status"])
    df["stage"] = (
        df["status"].astype("string").eq("In production").map({True: "Installed", False: "Pipeline"})
    )

    df["power_mw"] = pd.to_numeric(df["power_mw"], errors="coerce")
    if "n_turbines" in df.columns:
        df["n_turbines"] = pd.to_numeric(df["n_turbines"], errors="coerce")
        df["mw_per_turbine"] = (df["power_mw"] / df["n_turbines"]).round(2)
    else:
        df["n_turbines"] = pd.NA
        df["mw_per_turbine"] = pd.NA

    if "dist_coast" in df.columns:
        df["dist_coast_km"] = (pd.to_numeric(df["dist_coast"], errors="coerce") / 1000).round(2)
    else:
        df["dist_coast_km"] = pd.NA

    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    else:
        df["year"] = pd.NA

    before = len(df)
    df = df[df["power_mw"].notna() & (df["power_mw"] > 0)].copy()
    dropped = before - len(df)
    if dropped:
        logger.info("Dropped %d of %d records with no usable capacity", dropped, before)

    columns = [column for column in KEEP_COLUMNS if column in df.columns]
    return df[columns].sort_values(["country", "power_mw"], ascending=[True, False])


def summarise(df: pd.DataFrame) -> str:
    """Return the headline figures, for a caption or a project README."""
    installed = df.loc[df["stage"] == "Installed", "power_mw"].sum()
    pipeline = df.loc[df["stage"] == "Pipeline", "power_mw"].sum()
    no_year = int(df["year"].isna().sum())

    by_country = (
        df.groupby("country", observed=True)["power_mw"].sum().sort_values(ascending=False)
    )
    top = ", ".join(f"{name} ({value:,.0f} MW)" for name, value in by_country.head(3).items())

    median_distance = df.groupby("stage", observed=True)["dist_coast_km"].median()

    lines = [
        f"Projects: {len(df):,}",
        f"Installed capacity: {installed:,.0f} MW",
        f"Pipeline capacity: {pipeline:,.0f} MW",
        f"Top three countries by total capacity: {top}",
        f"Median distance to coast (km): {median_distance.to_dict()}",
        f"Projects with no recorded start year: {no_year:,}",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Local spatial file to read.")
    source.add_argument(
        "--wfs", action="store_true", help="Fetch the layer from the EMODnet WFS endpoint."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/processed/wind_farms_clean.csv"),
        help="Where to write the cleaned CSV.",
    )
    parser.add_argument("--summary", action="store_true", help="Print the headline figures.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        raw = load_windfarms(args.input, args.wfs)
        df = clean(raw)
    except (ValueError, KeyError, FileNotFoundError, ImportError) as error:
        logger.error("%s", error)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False)
    logger.info("Wrote %d records to %s", len(df), args.output)

    if args.summary:
        print(summarise(df))

    return 0


if __name__ == "__main__":
    sys.exit(main())
