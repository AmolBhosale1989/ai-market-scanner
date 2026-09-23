"""Equivalent finite OHLCV rules for PostgreSQL and pandas consumers."""
import numpy as np
import pandas as pd

FIELDS = ("open", "high", "low", "close", "volume")


def invalid_sql(alias: str) -> str:
    fields = [f"{alias}.{c}" for c in FIELDS]
    finite = " OR ".join(
        f"({c} IS NULL OR {c}::text IN ('NaN','Infinity','-Infinity'))" for c in fields
    )
    return (f"({finite} OR {alias}.open<=0 OR {alias}.high<=0 OR {alias}.low<=0 "
            f"OR {alias}.close<=0 OR {alias}.volume<0 "
            f"OR {alias}.high<GREATEST({alias}.open,{alias}.close,{alias}.low) "
            f"OR {alias}.low>LEAST({alias}.open,{alias}.close,{alias}.high))")


def invalid_rows(frame: pd.DataFrame) -> pd.Series:
    x = frame[list(FIELDS)].apply(pd.to_numeric, errors="coerce").astype(float)
    return (~np.isfinite(x).all(axis=1)
            | x[["open", "high", "low", "close"]].le(0).any(axis=1)
            | x.volume.lt(0)
            | x.high.lt(x[["open", "close", "low"]].max(axis=1))
            | x.low.gt(x[["open", "close", "high"]].min(axis=1)))
