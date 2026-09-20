from __future__ import annotations

import argparse
import ast
from pathlib import Path


PRODUCTION_MARKET_MODULES=(
    "main.py","broad_breakout.py","theme_live.py","sector_rotation.py",
    "momentum_signals.py","live.py","intraday.py","v3_live.py",
    "v3_live_refresh.py","order_flow.py","order_flow_strategy.py",
    "order_flow_validation.py","premarket.py","daily_pick.py",
)
PERSISTED_MARKET_INPUTS={"latest_scan.csv","all_candidates.csv","watchlist.csv"}


def _imports(tree: ast.AST) -> set[str]:
    values=set()
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node,ast.ImportFrom):
            values.add(("."*node.level)+(node.module or ""))
    return values


def audit(scanner_dir: Path) -> list[str]:
    violations=[]
    for name in PRODUCTION_MARKET_MODULES:
        path=scanner_dir/name
        text=path.read_text()
        tree=ast.parse(text,filename=str(path))
        imports=_imports(tree)
        if any(x=="yfinance" or x.startswith("yfinance.") for x in imports):
            violations.append(f"{name}: direct yfinance import")
        if any(x in {".data","scanner.data"} for x in imports):
            violations.append(f"{name}: provider adapter import outside warehouse ingestion")
        # CSVs remain valid publication artifacts, but these historical
        # candidate snapshots may never be accepted as production inputs.
        if name=="intraday.py":
            for forbidden in PERSISTED_MARKET_INPUTS:
                marker=f'OUTPUT_DIR/"{forbidden}"'
                if marker in text:
                    violations.append(f"{name}: persisted production input {forbidden}")
    return violations


def main():
    p=argparse.ArgumentParser(description="Audit the production PostgreSQL-only market-data boundary")
    p.add_argument("--scanner-dir",type=Path,default=Path(__file__).resolve().parent)
    args=p.parse_args()
    violations=audit(args.scanner_dir)
    if violations:
        raise RuntimeError("PROVIDER_BYPASS_AUDIT_FAILED:\n"+"\n".join(violations))
    print("PROVIDER_BYPASS_AUDIT_PASS production_market_modules=14")


if __name__ == "__main__":
    main()
