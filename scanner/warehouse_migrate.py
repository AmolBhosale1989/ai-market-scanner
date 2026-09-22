from __future__ import annotations

import os
from pathlib import Path

def apply_schema():
    if not os.getenv("DATABASE_URL","").strip():
        raise RuntimeError("DATABASE_URL is required for the bitemporal warehouse")
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], sslmode=os.getenv("PGSSLMODE","require")) as conn:
        with conn.cursor() as cur:
            migrations=sorted(Path("sql").glob("*.sql"))
            if not migrations:
                raise RuntimeError("WAREHOUSE_SCHEMA_MISSING: sql migrations not found")
            for migration in migrations:
                cur.execute(migration.read_text())
    print("POSTGRESQL_MARKET_AND_CONTROL_SCHEMA_READY")

if __name__=="__main__":
    apply_schema()
