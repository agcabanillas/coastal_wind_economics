![tests](https://github.com/agcabanillas/coastal-wind-economics/actions/workflows/ci.yml/badge.svg)

# Coastal economies and the offshore wind pipeline

Which European coastal regions carry the most proposed offshore wind capacity
relative to the size of their economy?

## Pipeline

1. `prepare_windfarms.py` cleans the EMODnet offshore wind point layer
2. `fetch_eurostat.py` pulls NUTS3 GDP and population from the Eurostat API
3. `assign_regions.py` assigns each project to its nearest NUTS3 region
4. `load_database.py` loads three tables into SQLite and exports the result
5. `sql/analysis.sql` holds the analysis, run with `run_query.py`

## Data sources

- EMODnet Human Activities, offshore wind farms, originator CETMAR, CC-BY 4.0
- Eurostat regional accounts, nama_10r_3gdp and demo_r_pjangrp3, reference year 2023
- Eurostat GISCO, NUTS 2024 boundaries, 1:1 million, EPSG:3035

## Decisions and limitations

- Both indicators are pinned to 2023 so that GDP and population describe the
  same year. Selecting each indicator's best-covered year independently gave
  GDP 2023 against population 2019, because older years still contain regions
  that no longer exist in the current classification.
- An earlier version took the first reference year that returned any data.
  Member states report regional accounts on different timetables, so that year
  covered a small group of early reporters only and the analysis silently
  narrowed to those countries. Coverage per year is now logged and compared.
- Projects are assigned to the nearest region within 150 km. 4 of 610 fall
  outside that cutoff and are left unassigned.
- Distances are computed in EPSG:3035, a European equal-area projection with
  units in metres, not in degrees.
- Coverage by stage: 610 projects and 454,535 MW in the source layer; 606
  projects and 451,760 MW assigned to a region; 540 projects and 395,393 MW
  in regions with GDP data. The ranked table covers 87 percent of proposed
  capacity.
- The United Kingdom and Norway are absent from Eurostat regional accounts.
  A further eight regions, five of them in the Netherlands, have no GDP match,
  most likely because their codes were revised between classification versions.
- Regional GDP publishes with a lag, so a 2040 project pipeline is compared
  against a 2023 economy.

## Dashboard

[Tableau link goes here]

## Running it

    conda env create -f environment.yml
    conda activate coastal-wind
    python prepare_windfarms.py --input data/raw/windfarms.csv
    python fetch_eurostat.py --year 2023
    python assign_regions.py
    python load_database.py
    pytest
