"""Load the wind farm, region and economic tables into a SQLite database.

Takes the outputs of prepare_windfarms.py, assign_regions.py and
fetch_eurostat.py and writes them into a single relational database, so that
the analysis itself can be expressed as SQL rather than as further pandas
code. The three tables share one key, the NUTS3 region code.

Schema
------
    wind_farms   one row per offshore wind project, with its assigned region
    regions      one row per NUTS3 region that has at least one project
    economics    one row per NUTS3 region, with GDP, population, unemployment

Usage
-----
    python load_database.py
    python load_database.py --export data/processed/top_regions.csv
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

import pandas as pd

HEADLINE_QUERY = """
WITH regional_capacity AS (
    SELECT
        w.nuts3_id,
        COUNT(*)                                          AS project_count,
        SUM(w.power_mw)                                   AS total_mw,
        SUM(CASE WHEN w.stage = 'Pipeline'  THEN w.power_mw ELSE 0 END) AS pipeline_mw,
        SUM(CASE WHEN w.stage = 'Installed' THEN w.power_mw ELSE 0 END) AS installed_mw
    FROM wind_farms AS w
    WHERE w.nuts3_id IS NOT NULL
    GROUP BY w.nuts3_id
)
SELECT
    c.nuts3_id,
    r.nuts3_name,
    r.country_code,
    c.project_count,
    ROUND(c.total_mw, 1)                                  AS total_mw,
    ROUND(c.pipeline_mw, 1)                               AS pipeline_mw,
    ROUND(c.installed_mw, 1)                              AS installed_mw,
    e.gdp_meur,
    e.population,
    ROUND(c.total_mw / (e.gdp_meur / 1000.0), 2)          AS mw_per_bn_eur_gdp,
    RANK() OVER (
        PARTITION BY r.country_code
        ORDER BY c.total_mw / (e.gdp_meur / 1000.0) DESC
    )                                                     AS rank_in_country
FROM regional_capacity AS c
JOIN regions   AS r ON r.nuts3_id = c.nuts3_id
JOIN economics AS e ON e.nuts3_id = c.nuts3_id
WHERE e.gdp_meur IS NOT NULL AND e.gdp_meur > 0
ORDER BY mw_per_bn_eur_gdp DESC
"""

logger = logging.getLogger(__name__)


def read_required(path: Path, label: str) -> pd.DataFrame:
    """Read a CSV that the pipeline cannot proceed without."""
    if not path.exists():
        raise FileNotFoundError(f"{label} not found at {path}. Run the earlier script first.")
    return pd.read_csv(path)


def build_economics(raw_dir: Path) -> pd.DataFrame:
    """Merge the Eurostat indicator files into one row per region.

    Each indicator is fetched separately because they are separate Eurostat
    datasets with different dimensions. They are combined here on the region
    code with an outer join, so a region missing from one indicator still
    appears with a null rather than disappearing entirely.
    """
    indicators = {
        "gdp": "gdp_meur",
        "population": "population",
        "unemployment": "unemployment_rate",
    }

    merged: pd.DataFrame | None = None
    for name, column in indicators.items():
        path = raw_dir / f"eurostat_{name}.csv"
        if not path.exists():
            logger.warning("%s not found, continuing without it", path)
            continue

        df = pd.read_csv(path)
        if column not in df.columns:
            logger.warning("%s has no %s column, skipping", path, column)
            continue

        year_column = f"{name}_year"
        keep = df[["geo", column]].rename(columns={"geo": "nuts3_id"})
        if "time" in df.columns:
            keep[year_column] = df["time"]

        keep = keep.drop_duplicates(subset="nuts3_id")
        merged = keep if merged is None else merged.merge(keep, on="nuts3_id", how="outer")

    if merged is None:
        raise FileNotFoundError("No Eurostat files found. Run fetch_eurostat.py first.")

    logger.info("Economics table: %d regions", len(merged))
    return merged


def build_tables(processed_dir: Path, raw_dir: Path) -> dict[str, pd.DataFrame]:
    """Assemble the three tables that go into the database."""
    farms = read_required(processed_dir / "wind_farms_with_region.csv", "Joined wind farms")

    regions = (
        farms.loc[farms["nuts3_id"].notna(), ["nuts3_id", "nuts3_name", "country_code"]]
        .drop_duplicates(subset="nuts3_id")
        .sort_values("nuts3_id")
        .reset_index(drop=True)
    )

    farm_columns = [
        column
        for column in [
            "name", "country", "status", "stage", "year", "power_mw", "n_turbines",
            "dist_coast_km", "nuts3_id", "distance_to_region_km", "longitude", "latitude",
        ]
        if column in farms.columns
    ]

    logger.info("Wind farms table: %d rows", len(farms))
    logger.info("Regions table: %d rows", len(regions))

    return {
        "wind_farms": farms[farm_columns],
        "regions": regions,
        "economics": build_economics(raw_dir),
    }


def write_database(tables: dict[str, pd.DataFrame], destination: Path) -> None:
    """Write the tables to SQLite, replacing any previous contents."""
    destination.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(destination) as connection:
        for name, df in tables.items():
            df.to_sql(name, connection, if_exists="replace", index=False)
            logger.info("Wrote table %s (%d rows)", name, len(df))

        # Indexes on the join key. With tables this small the query planner
        # would cope without them, but declaring the access path is the habit
        # that matters once a table has millions of rows.
        connection.execute("CREATE INDEX IF NOT EXISTS idx_farms_region ON wind_farms(nuts3_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_econ_region ON economics(nuts3_id)")
        connection.commit()


def export_headline(database: Path, destination: Path) -> pd.DataFrame:
    """Run the headline query and write the result for the dashboard."""
    with sqlite3.connect(database) as connection:
        result = pd.read_sql_query(HEADLINE_QUERY, connection)

    destination.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(destination, index=False)
    logger.info("Wrote %d rows to %s", len(result), destination)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--processed-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--database", type=Path, default=Path("data/processed/coastal_wind.db"))
    parser.add_argument(
        "--export",
        type=Path,
        default=Path("data/processed/regional_exposure.csv"),
        help="Where to write the headline query result.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    try:
        tables = build_tables(args.processed_dir, args.raw_dir)
        write_database(tables, args.database)
        result = export_headline(args.database, args.export)
    except (FileNotFoundError, KeyError, ValueError, sqlite3.Error) as error:
        logger.error("%s", error)
        return 1

    if not result.empty:
        top = result.head(5)[["nuts3_name", "country_code", "total_mw", "mw_per_bn_eur_gdp"]]
        print(top.to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
