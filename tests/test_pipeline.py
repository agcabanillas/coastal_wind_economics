"""Tests for the coastal wind pipeline.

Each test here exists because the behaviour it checks either went wrong once
or would go wrong silently. Silent failures are the ones worth testing: a
crash announces itself, a swapped coordinate does not.
"""

from __future__ import annotations

import pandas as pd
import pytest

import assign_regions
import fetch_eurostat
import prepare_windfarms


# ---------------------------------------------------------------------------
# Coordinate handling
# ---------------------------------------------------------------------------

def test_wkt_point_is_parsed_into_two_columns():
    series = pd.Series(["POINT (-3.8 43.5)", "POINT (2.84 51.69)"])
    result = prepare_windfarms._coordinates_from_wkt(series)

    assert list(result.columns) == ["longitude", "latitude"]
    assert result.loc[0, "longitude"] == pytest.approx(-3.8)
    assert result.loc[0, "latitude"] == pytest.approx(43.5)


def test_separate_coordinate_columns_are_found_under_aliases():
    df = pd.DataFrame({"x": [-3.8], "y": [43.5], "name": ["A"]})
    result = prepare_windfarms.resolve_coordinates(df)

    assert result.loc[0, "longitude"] == pytest.approx(-3.8)
    assert result.loc[0, "latitude"] == pytest.approx(43.5)


def test_missing_coordinates_raise_with_the_available_columns_named():
    df = pd.DataFrame({"name": ["A"], "power_mw": [100]})

    with pytest.raises(KeyError) as error:
        prepare_windfarms.resolve_coordinates(df)

    assert "power_mw" in str(error.value)


def test_swapped_coordinates_are_detected_and_corrected():
    """The Somalia bug: European points with longitude and latitude reversed."""
    df = pd.DataFrame(
        {
            "longitude": [43.5, 51.69, 57.4],
            "latitude": [-3.8, 2.84, -2.1],
        }
    )

    result = assign_regions.validate_coordinates(df)

    assert result["longitude"].between(-32, 45).all()
    assert result["latitude"].between(25, 75).all()


def test_correct_coordinates_are_left_alone():
    df = pd.DataFrame({"longitude": [-3.8, 2.84], "latitude": [43.5, 51.69]})

    result = assign_regions.validate_coordinates(df)

    assert result["longitude"].tolist() == [-3.8, 2.84]


# ---------------------------------------------------------------------------
# Cleaning rules
# ---------------------------------------------------------------------------

def test_unrecognised_status_becomes_unknown_rather_than_being_dropped():
    statuses = pd.Series(["Production", "Planned", "Something New"])

    result = pd.Series(prepare_windfarms.group_status(statuses)).astype("string")

    assert result.tolist() == ["In production", "Planned", "Unknown"]


def test_projects_without_capacity_are_dropped():
    df = pd.DataFrame(
        {
            "name": ["A", "B", "C"],
            "country": ["Spain", "Spain", "UK"],
            "status": ["Production", "Planned", "Planned"],
            "power_mw": [100.0, None, 0.0],
            "longitude": [-3.8, -3.9, 1.0],
            "latitude": [43.5, 43.6, 54.0],
        }
    )

    result = prepare_windfarms.clean(df)

    assert len(result) == 1
    assert result.iloc[0]["name"] == "A"


def test_in_production_is_labelled_installed_and_everything_else_pipeline():
    df = pd.DataFrame(
        {
            "name": ["A", "B"],
            "country": ["Spain", "Spain"],
            "status": ["Production", "Approved"],
            "power_mw": [100.0, 200.0],
            "longitude": [-3.8, -3.9],
            "latitude": [43.5, 43.6],
        }
    )

    result = prepare_windfarms.clean(df).set_index("name")

    assert result.loc["A", "stage"] == "Installed"
    assert result.loc["B", "stage"] == "Pipeline"


# ---------------------------------------------------------------------------
# Eurostat response handling
# ---------------------------------------------------------------------------

def _jsonstat_payload() -> dict:
    """A minimal JSON-stat 2.0 response: one unit, three regions, one year."""
    return {
        "id": ["unit", "geo", "time"],
        "size": [1, 3, 1],
        "dimension": {
            "unit": {"category": {"index": {"MIO_EUR": 0}}},
            "geo": {"category": {"index": {"ES": 0, "ES130": 1, "FR101": 2}}},
            "time": {"category": {"index": {"2023": 0}}},
        },
        "value": {"0": 1200000, "1": 5432.1, "2": 9876.5},
    }


def test_jsonstat_positions_decode_to_the_right_categories():
    result = fetch_eurostat.decode_jsonstat(_jsonstat_payload(), "gdp_meur")

    assert len(result) == 3
    row = result[result["geo"] == "ES130"].iloc[0]
    assert row["gdp_meur"] == pytest.approx(5432.1)
    assert row["time"] == "2023"


def test_jsonstat_nulls_are_skipped():
    payload = _jsonstat_payload()
    payload["value"] = [1200000, None, 9876.5]

    result = fetch_eurostat.decode_jsonstat(payload, "gdp_meur")

    assert len(result) == 2
    assert "ES130" not in result["geo"].tolist()


def test_only_five_character_nuts3_codes_are_kept():
    df = pd.DataFrame({"geo": ["ES", "ES1", "ES13", "ES130"], "gdp_meur": [1, 2, 3, 4]})

    result = fetch_eurostat.keep_nuts3(df)

    assert result["geo"].tolist() == ["ES130"]
