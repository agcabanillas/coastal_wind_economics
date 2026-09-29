"""Run SQL against the coastal wind database and print the results.

The VS Code SQLite extensions vary in whether they can execute queries, so
this gives the project its own way to run them, independent of the editor.
It also means every query is run the same way by anyone who clones the repo.

Usage
-----
    python run_query.py sql/analysis.sql            run every query in a file
    python run_query.py sql/analysis.sql --only 5   run just the fifth query
    python run_query.py "SELECT COUNT(*) FROM wind_farms"
    python run_query.py sql/analysis.sql --only 5 --csv out.csv
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

DEFAULT_DATABASE = Path("data/processed/coastal_wind.db")


def split_statements(sql: str) -> list[str]:
    """Split a SQL file into individual statements, dropping comment lines.

    Comments are stripped before splitting so that a semicolon inside a
    comment does not end a statement early.
    """
    without_comments = re.sub(r"--[^\n]*", "", sql)
    return [statement.strip() for statement in without_comments.split(";") if statement.strip()]


def first_line(statement: str) -> str:
    """A short label for a statement, for printing above its result."""
    collapsed = " ".join(statement.split())
    return collapsed[:70] + ("..." if len(collapsed) > 70 else "")


def run(database: Path, statements: list[str], limit: int) -> list[pd.DataFrame]:
    """Execute each statement and return the results as DataFrames."""
    results = []
    with sqlite3.connect(database) as connection:
        for position, statement in enumerate(statements, start=1):
            print(f"\n--- [{position}] {first_line(statement)}")
            try:
                frame = pd.read_sql_query(statement, connection)
            except pd.errors.DatabaseError as error:
                print(f"    FAILED: {error}")
                results.append(pd.DataFrame())
                continue

            if frame.empty:
                print("    (no rows)")
            else:
                print(frame.head(limit).to_string(index=False))
                if len(frame) > limit:
                    print(f"    ... {len(frame) - limit} more rows")
            results.append(frame)

    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", help="Path to a .sql file, or a SQL statement in quotes.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--only", type=int, default=None, help="Run only the Nth statement in the file.")
    parser.add_argument("--limit", type=int, default=20, help="Rows to print per result.")
    parser.add_argument("--csv", type=Path, default=None, help="Write the last result to this CSV.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.database.exists():
        print(f"Database not found at {args.database}. Run load_database.py first.")
        return 1

    candidate = Path(args.query)
    if candidate.suffix.lower() == ".sql":
        if not candidate.exists():
            print(f"SQL file not found: {candidate}")
            return 1
        statements = split_statements(candidate.read_text(encoding="utf-8"))
    else:
        statements = [args.query]

    if args.only is not None:
        if not 1 <= args.only <= len(statements):
            print(f"--only must be between 1 and {len(statements)}")
            return 1
        statements = [statements[args.only - 1]]

    results = run(args.database, statements, args.limit)

    if args.csv and results and not results[-1].empty:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        results[-1].to_csv(args.csv, index=False)
        print(f"\nWrote {len(results[-1])} rows to {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
