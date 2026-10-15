"""Create schemas and load the mock SAP CSVs into Postgres."""
import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parents[1]
TABLES = ["lfa1", "ekko", "ekpo", "mseg", "rbkp", "rseg", "ekbe"]   # FK order

with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    # Drop and recreate rather than relying on "CREATE TABLE IF NOT EXISTS" to
    # pick up schema changes -- this is mock/demo data, not something with
    # rows worth preserving across a schema edit, and IF NOT EXISTS silently
    # keeps stale columns otherwise (hit this directly: added columns to
    # sap.lfa1 and the old table just stayed as-is).
    conn.execute("DROP SCHEMA IF EXISTS ap CASCADE")
    conn.execute("DROP SCHEMA IF EXISTS sap CASCADE")
    conn.execute((ROOT / "db" / "schema.sql").read_text())
    for t in TABLES:
        with open(ROOT / "data" / "sap" / f"{t}.csv") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            print(f"  sap.{t}: 0 rows (skipped)")
            continue
        cols = list(rows[0].keys())
        if t == "ekbe":
            cols = [c for c in cols if c != "id"]   # id is SERIAL, not in the CSV anyway
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO sap.{t} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                [tuple(r[c] for c in cols) for r in rows])
        print(f"  sap.{t}: {len(rows)} rows")
print("Database ready.")
