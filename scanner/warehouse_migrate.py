from __future__ import annotations

import os
from pathlib import Path

def apply_schema():
    if not os.getenv("DATABASE_URL","").strip():
        raise RuntimeError("DATABASE_URL is required for the bitemporal warehouse")
    import psycopg
    sql=Path("sql/001_bitemporal_warehouse.sql").read_text()
    with psycopg.connect(os.environ["DATABASE_URL"], sslmode=os.getenv("PGSSLMODE","require")) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print("BITEMPORAL_WAREHOUSE_SCHEMA_READY")

if __name__=="__main__":
    apply_schema()
