"""Create schemas and load the mock SAP CSVs into Postgres."""
import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parents[1]
TABLES = ["lfa1", "ekko", "ekpo", "mseg"]          # FK order

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    conn.execute((ROOT / "db" / "schema.sql").read_text())
    for t in reversed(TABLES):
        conn.execute(f"TRUNCATE sap.{t} CASCADE")
    for t in TABLES:
        with open(ROOT / "data" / "sap" / f"{t}.csv") as f:
            rows = list(csv.DictReader(f))
        cols = list(rows[0].keys())
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO sap.{t} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                [tuple(r[c] for c in cols) for r in rows])
        print(f"  sap.{t}: {len(rows)} rows")
print("Database ready.")
