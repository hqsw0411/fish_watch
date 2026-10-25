"""Check the portable deployment files and, optionally, a running server.

Uses Python's standard library; does not require pandas, Node.js or data downloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import runtime  # make workspace imports independent of cwd
from backend.app.paths import resolve_sample_dir

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def check_files(root: Path) -> dict:
    package_manifest = root / "deployment-manifest.json"
    verified = 0
    if package_manifest.is_file():
        manifest = json.loads(package_manifest.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError("Package path escapes project root")
            if not path.is_file() or digest(path) != item["sha256"]:
                raise ValueError(f"Package file missing or changed: {item['path']}")
            verified += 1
    directory = resolve_sample_dir(root)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if digest(directory / "sample.csv") != manifest["sha256"]:
        raise ValueError("Sample CSV differs from its manifest")
    index = root / "frontend/dist/index.html"
    html = index.read_text(encoding="utf-8")
    for path in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
        if not (root / "frontend/dist" / path.lstrip("/")).is_file():
            raise ValueError(f"Frontend asset missing: {path}")
    if not (root / "frontend/dist/land.geojson").is_file():
        raise ValueError("Offline basemap missing")
    if not (root / "docs/data_dictionary.json").is_file():
        raise ValueError("Data dictionary missing")
    database = root / "data/database/fisheries_forecast.sqlite3"
    research = None
    if database.is_file():
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Research database quick_check failed")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Research database foreign key violation")
            research = {
                "tables": conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchone()[0],
                "effort_cell_period": conn.execute("SELECT COUNT(*) FROM effort_cell_period").fetchone()[0],
            }
    return {"files_verified": verified, "sample_id": manifest["id"],
            "rows": manifest["rows"], "research_database": research}


def check_http(root: Path, base_url: str) -> dict:
    base = base_url.rstrip("/")
    def request(path: str):
        with urlopen(base + path, timeout=30) as response:
            if response.status != 200:
                raise ValueError(f"HTTP failure: {path}")
            return response.read()
    health = json.loads(request("/api/health"))
    if health.get("status") != "ok":
        raise ValueError("API is not healthy")
    catalog = json.loads(request("/api/catalog"))
    month = catalog["months"][0]
    west, south, _, _ = catalog["bbox"]
    step = catalog["grid_degrees"]
    params = {"month": month, "west": west, "south": south,
              "east": west + step, "north": south + step}
    sample = json.loads(request("/api/sample?" + urlencode(params)))
    if len(sample["cells"]) != 1 or sample["cells"][0]["month"] != month:
        raise ValueError("Point window did not return the expected single grid cell")
    full = json.loads(request("/api/sample?" + urlencode(
        {"month": month, **dict(zip(("west", "south", "east", "north"), catalog["bbox"]))}
    )))
    summary = full["summary"]
    if summary["cells"] != catalog["cells_per_month"]:
        raise ValueError("Full-month API row count mismatch")
    expected = catalog["quality"][month]["fishing_hours"]
    if not math.isclose(summary["fishing_hours"], expected, abs_tol=1e-3):
        raise ValueError("Full-month API effort total mismatch")
    temperature = json.loads(request("/api/temperature?" + urlencode(
        {"month": month, "baseline": "regional", "percentile": 75}
    )))
    if temperature.get("month") != month:
        raise ValueError("Temperature layer month mismatch")
    page = request("/").decode("utf-8")
    if '<div id="root">' not in page:
        raise ValueError("Web UI is not served")
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page)
    if not assets:
        raise ValueError("Built UI assets not referenced")
    for asset in assets:
        request(asset)
    request("/land.geojson")
    return {"health": health, "window_cells": len(sample["cells"]),
            "month_cells": summary["cells"], "fishing_hours": summary["fishing_hours"],
            "temperature": "ok", "web_assets": len(assets), "offline_basemap": "ok"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Also check the HTTP service, e.g. http://127.0.0.1:8000")
    args = parser.parse_args()
    report = {"files": check_files(ROOT)}
    if args.url:
        report["http"] = check_http(ROOT, args.url)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
