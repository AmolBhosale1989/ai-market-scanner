from __future__ import annotations

import argparse
import ast
from pathlib import Path


PROVIDER_BOUNDARIES = {
    "data.py", "warehouse_refresh.py", "universe.py", "events.py", "earnings_intel.py",
    "catalysts.py", "options_microstructure.py",
}
FILE_IO_ALLOWLIST = {"control_plane.py", "warehouse_migrate.py", "production_audit.py"}


def _imports(tree: ast.AST) -> set[str]:
    values = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            values.add(("." * node.level) + (node.module or ""))
    return values


def audit(root: Path) -> list[str]:
    violations: list[str] = []
    if root.name == "scanner":
        root = root.parent
    tabular_suffix = "." + "c" + "s" + "v"
    json_lines_suffix = "." + "nd" + "json"
    legacy_branch = "scan" + "-data"
    for path in sorted((root / "scanner").rglob("*.py")) + [root / "app.py"]:
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        imports = _imports(tree)
        name = path.name
        relative = path.relative_to(root)
        if name not in PROVIDER_BOUNDARIES:
            if any(value == "yfinance" or value.startswith("yfinance.") for value in imports):
                violations.append(f"{relative}: provider import outside ingestion boundary")
            if any(value in {".data", "scanner.data"} for value in imports):
                violations.append(f"{relative}: provider adapter import outside ingestion boundary")
        if tabular_suffix in text.lower() or "read_" + "csv" in text or "to_" + "csv" in text:
            violations.append(f"{relative}: legacy tabular-file dependency")
        if legacy_branch in text:
            violations.append(f"{relative}: legacy publication branch dependency")
        if name not in FILE_IO_ALLOWLIST:
            forbidden = ("OUTPUT_DIR", "DATA_DIR", ".write_text(", ".read_text(", ".open(")
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{relative}: file control/data-plane API {marker}")
    workflow_dir = root / ".github" / "workflows"
    for path in sorted(workflow_dir.glob("*.yml")):
        text = path.read_text()
        relative = path.relative_to(root)
        for marker in (tabular_suffix, legacy_branch, "upload-artifact", "actions/cache", "git worktree",
                       "git push", "continue-on-error"):
            if marker in text:
                violations.append(f"{relative}: forbidden workflow publication mechanism {marker}")
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in {tabular_suffix, json_lines_suffix}:
            violations.append(f"{path.relative_to(root)}: legacy tabular artifact")
    return violations


def main():
    parser = argparse.ArgumentParser(description="Audit the PostgreSQL-only repository boundary")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    violations = audit(args.root)
    if violations:
        raise RuntimeError("POSTGRES_BOUNDARY_AUDIT_FAILED:\n" + "\n".join(violations))
    print("POSTGRES_BOUNDARY_AUDIT_PASS")


if __name__ == "__main__":
    main()
