"""Read-only provider comparison. Never writes warehouse or fills absent bars."""
import json, time
from datetime import datetime, timezone
import pandas as pd
import yfinance as yf
from scanner.data import _download_once
from scanner.provider_diagnostics import describe_response

def report(symbol, frame, method, started, elapsed):
    received = datetime.now(timezone.utc)
    frame = frame.dropna(subset=["Open", "High", "Low", "Close"]) if frame is not None and not frame.empty else frame
    record = describe_response(symbol, frame, period="2d", interval="5m",
        started=started, received=received, elapsed=elapsed)
    record["method"] = method
    print("COMPARISON", json.dumps(record), flush=True)
    if frame is not None and not frame.empty:
        print("TAIL", symbol, method, frame.tail(12).to_json(date_format="iso"), flush=True)

symbols = ["SKYY", "FINX", "SPY"]
started = datetime.now(timezone.utc); tick = time.monotonic()
batch = _download_once(symbols, "2d", "5m")
elapsed = time.monotonic()-tick
for symbol in symbols:
    report(symbol, batch.get(symbol), "production_batch", started, elapsed)
for symbol in symbols:
    started = datetime.now(timezone.utc); tick = time.monotonic()
    try:
        frame = yf.Ticker(symbol).history(period="2d", interval="5m",
            auto_adjust=True, prepost=False, timeout=20, raise_errors=True)
        report(symbol, frame, "individual_history", started, time.monotonic()-tick)
    except Exception as exc:
        print("PROVIDER_ERROR", symbol, type(exc).__name__, str(exc), flush=True)
