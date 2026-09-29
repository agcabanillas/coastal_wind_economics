"""Fetch NUTS3 regional economic indicators from the Eurostat statistics API.

Downloads regional GDP, population and unemployment for NUTS3 regions and
writes one tidy CSV per indicator. The API serves JSON-stat 2.0, a compact
positional format in which the values arrive as a flat list and the dimension
definitions say what each position means, so the response is decoded here into
ordinary rows before anything else touches it.

Data source: Eurostat, https://ec.europa.eu/eurostat
Licence: Eurostat data is reusable with attribution.

Usage
-----
    python fetch_eurostat.py
    python fetch_eurostat.py --year 2022
    python fetch_eurostat.py --outdir data/raw
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

# Each entry: the Eurostat dataset code, the dimension filters that pin it down
# to a single series per region, and the column name to give the value.
DATASETS: dict[str, dict] = {
    "gdp": {
        "code": "nama_10r_3gdp",
        "params": {"unit": "MIO_EUR"},
        "value_name": "gdp_meur",
        "description": "GDP at current market prices, million EUR",
    },
    "population": {
        "code": "demo_r_pjangrp3",
        "params": {"sex": "T", "age": "TOTAL", "unit": "NR"},
        "value_name": "population",
        "description": "Population on 1 January, persons",
    },
}

# Years to try. Regional accounts lag by roughly two years and member states
# report on different timetables, so the newest year present is usually covered
# by a few countries only. All of these are fetched and compared on coverage.
CANDIDATE_YEARS: list[int] = [2024, 2023, 2022, 2021, 2020, 2019]

REQUEST_TIMEOUT = 60

logger = logging.getLogger(__name__)


def decode_jsonstat(payload: dict, value_name: str) -> pd.DataFrame:
    """Turn a JSON-stat 2.0 response into a tidy DataFrame.

    JSON-stat stores the numbers in a flat map keyed by position. The position
    is a single integer that encodes one combination of every dimension, laid
    out in the order given by ``id`` with the lengths given by ``size``. To
    recover the combination, the position is divided down by the stride of each
    dimension in turn, exactly like converting a flat array index into
    multi-dimensional subscripts.

    Parameters
    ----------
    payload
        The parsed JSON body of the API response.
    value_name
        Column name to give the numeric value.

    Returns
    -------
    pandas.DataFrame
        One row per observation, one column per dimension, plus the value.
    """
    dimension_ids: list[str] = payload["id"]
    sizes: list[int] = payload["size"]
    dimensions = payload["dimension"]

    # Category codes for each dimension, ordered by their index position.
    categories: dict[str, list[str]] = {}
    for dimension_id in dimension_ids:
        index = dimensions[dimension_id]["category"]["index"]
        if isinstance(index, dict):
            ordered = sorted(index.items(), key=lambda pair: pair[1])
            categories[dimension_id] = [code for code, _ in ordered]
        else:
            categories[dimension_id] = list(index)

    # Stride of a dimension is the product of the sizes of everything after it.
    strides: list[int] = []
    running = 1
    for size in reversed(sizes):
        strides.insert(0, running)
        running *= size

    values = payload["value"]
    if isinstance(values, list):
        values = {str(position): value for position, value in enumerate(values)}

    rows = []
    for position, value in values.items():
        if value is None:
            continue
        remainder = int(position)
        row = {}
        for dimension_id, stride in zip(dimension_ids, strides):
            row[dimension_id] = categories[dimension_id][remainder // stride]
            remainder = remainder % stride
        row[value_name] = value
        rows.append(row)

    if not rows:
        return pd.DataFrame(columns=[*dimension_ids, value_name])

    return pd.DataFrame(rows)


def fetch_dataset(code: str, params: dict, year: int) -> dict:
    """Request one Eurostat dataset for one year and return the parsed JSON."""
    query = {"format": "JSON", "lang": "EN", "time": str(year), **params}
    url = f"{BASE_URL}/{code}"
    logger.info("Requesting %s for %s", code, year)

    response = requests.get(url, params=query, timeout=REQUEST_TIMEOUT)
    if response.status_code == 400:
        raise ValueError(
            f"Eurostat rejected the query for {code}. This usually means one of the "
            f"dimension filters does not exist in this dataset. Filters sent: {params}"
        )
    response.raise_for_status()
    return json.loads(response.text)


def keep_nuts3(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NUTS3 rows.

    Eurostat region codes nest by length: 2 characters is a country, 3 is
    NUTS1, 4 is NUTS2 and 5 is NUTS3. The same table holds all levels, so the
    finest level is selected by code length rather than by a separate field.
    """
    if "geo" not in df.columns:
        raise KeyError(f"No 'geo' dimension in the response. Columns: {list(df.columns)}")
    return df[df["geo"].str.len() == 5].copy()


def fetch_indicator(name: str, spec: dict, year: int | None) -> pd.DataFrame:
    """Fetch one indicator and return the year with the best regional coverage.

    Member states report regional accounts on different timetables, so the most
    recent year present in a table is typically populated for a handful of fast
    reporters only. Taking the first year that returns any data therefore
    silently restricts the analysis to those countries. This instead collects
    every candidate year and keeps the one covering the most regions, unless a
    specific year was requested.
    """
    if year:
        payload = fetch_dataset(spec["code"], spec["params"], year)
        df = keep_nuts3(decode_jsonstat(payload, spec["value_name"]))
        if df.empty:
            raise ValueError(f"No NUTS3 data for {name} in {year}")
        logger.info("%s: %d NUTS3 regions for %s (requested)", name, len(df), year)
        return select_columns(df, spec["value_name"])

    by_year: dict[int, pd.DataFrame] = {}
    for candidate in CANDIDATE_YEARS:
        try:
            payload = fetch_dataset(spec["code"], spec["params"], candidate)
        except (ValueError, requests.HTTPError) as error:
            logger.warning("%s failed for %s: %s", name, candidate, error)
            continue

        df = decode_jsonstat(payload, spec["value_name"])
        if df.empty:
            continue

        df = keep_nuts3(df)
        if not df.empty:
            by_year[candidate] = df

    if not by_year:
        raise ValueError(f"No data returned for {name} in any of {CANDIDATE_YEARS}")

    coverage = {candidate: len(df) for candidate, df in sorted(by_year.items(), reverse=True)}
    logger.info("%s regional coverage by year: %s", name, coverage)

    best_count = max(coverage.values())
    # Among years within 5 percent of the best coverage, prefer the most recent.
    good_enough = [
        candidate for candidate, count in coverage.items() if count >= best_count * 0.95
    ]
    chosen = max(good_enough)

    logger.info("%s: using %s with %d NUTS3 regions", name, chosen, coverage[chosen])
    return select_columns(by_year[chosen], spec["value_name"])


def select_columns(df: pd.DataFrame, value_name: str) -> pd.DataFrame:
    """Keep only the region, the period and the value."""
    columns = ["geo", "time", value_name]
    return df[[column for column in columns if column in df.columns]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--year", type=int, default=None, help="Reference year. Omit to use the most recent available."
    )
    parser.add_argument(
        "--outdir", type=Path, default=Path("data/raw"), help="Where to write the CSV files."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    failures = []
    for name, spec in DATASETS.items():
        try:
            df = fetch_indicator(name, spec, args.year)
        except (ValueError, requests.RequestException, KeyError) as error:
            logger.error("%s: %s", name, error)
            failures.append(name)
            continue

        destination = args.outdir / f"eurostat_{name}.csv"
        df.to_csv(destination, index=False)
        logger.info("Wrote %d rows to %s", len(df), destination)

    if failures:
        logger.error("Failed: %s", ", ".join(failures))
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
