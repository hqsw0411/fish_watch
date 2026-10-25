"""Build a versioned multi-year monthly dataset, keeping at most one month in memory.

Example: python -m tools.data.build_regional_dataset --config config/east_asia_2012_2024.json
Use --plan for a read-only size/source preview. Existing month partitions are
reused only after checking the config fingerprint and the CSV checksum.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
import shutil
from pathlib import Path
import tempfile

from tools import paths as runtime
import pandas as pd
import xarray as xr


from tools.data.build_sample import build, ENVIRONMENT_FIELDS
from tools.data.fetch_effort import fetch as fetch_effort
from tools.data.fetch_environment import fetch as fetch_environment
from backend.app.store import ensure_database, temperature_scale


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def config_fingerprint(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def describe(config: dict) -> dict:
    west, south, east, north = config["bbox"]
    step = float(config["grid_degrees"])
    if step != .1 or config["crs"] != "EPSG:4326":
        raise ValueError("This pipeline accepts GFW monthly 0.1 degree EPSG:4326 grids only")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Invalid non-crossing geographic bbox")
    if any(abs(value / step - round(value / step)) > 1e-7 for value in config["bbox"]):
        raise ValueError("BBox must align with the GFW lower-left grid")
    months = [str(month) for month in pd.period_range(config["start_month"], config["end_month"], freq="M")]
    if not months:
        raise ValueError("Empty month range")
    cells = round((east - west) / step) * round((north - south) / step)
    return {"id": config["id"], "bbox": config["bbox"], "months": months,
            "cells_per_month": cells, "rows": cells * len(months),
            "output_dir": config["output_dir"], "grid_degrees": step,
            "note": "Rectangular grid includes land and missing-data cells; valid observations are counted separately"}


def annual_config(config: dict, year: int) -> dict:
    result = deepcopy(config)
    result["start_month"] = max(config["start_month"], f"{year}-01")
    result["end_month"] = min(config["end_month"], f"{year}-12")
    result["gfw_zip"] = config["gfw_archive_pattern"].format(year=year)
    for source in result["environment_sources"]:
        source["path"] = source["path"].format(year=year)
    return result


def check_environment(config: dict) -> None:
    expected = set(str(p) for p in pd.period_range(config["start_month"], config["end_month"], freq="M"))
    config["_input_sha256"] = {config["gfw_zip"]: digest(runtime.ROOT / config["gfw_zip"])}
    for source in config["environment_sources"]:
        path = runtime.ROOT / source["path"]
        audit = json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
        west, south, east, north = config["bbox"]
        if audit.get("dataset_id") != source["dataset_id"] or audit.get("bbox_with_margin") != [west - .5, south - .5, east + .5, north + .5]:
            raise ValueError(f"Environment download audit differs from requested product/bbox: {path}")
        if digest(path) != audit.get("sha256"):
            raise ValueError(f"Environment checksum mismatch: {path}")
        config["_input_sha256"][source["path"]] = audit["sha256"]
        with xr.open_dataset(path) as data:
            actual = set(str(p) for p in pd.DatetimeIndex(data.time.values).to_period("M"))
            if expected - actual:
                raise ValueError(f"Environment product missing months: {source['dataset_id']} {sorted(expected - actual)}")
            for variable in source["variables"]:
                if variable not in data:
                    raise ValueError(f"Environment variable missing: {variable}")


def build_partition(config: dict, month: str, base: Path) -> dict:
    monthly = deepcopy(config)
    monthly.update(start_month=month, end_month=month, id=f"{config['id']}_{month}")
    monthly["output_dir"] = str(base / "partitions" / month)
    directory = runtime.ROOT / monthly["output_dir"]
    fingerprint = config_fingerprint(monthly)
    marker = directory / "build-config.sha256"
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file() and marker.is_file() and marker.read_text().strip() == fingerprint:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if digest(directory / "sample.csv") == manifest["sha256"]:
            print(f"Verified partition {month}", flush=True)
            return manifest
    with redirect_stdout(io.StringIO()):
        manifest = build(monthly)
    if not manifest["sources"]["gfw"]["official_checksum_verified"]:
        raise ValueError(f"Official GFW metadata required to verify partition {month}")
    for field in ENVIRONMENT_FIELDS:
        if manifest["quality"][month]["valid_environment"][field] == 0:
            raise ValueError(f"No valid {field} observations in {month}")
    # Missing cells along coasts/land remain NULL; check_environment already
    # rejected absent source months. Do not invent observations to fill land.
    marker.write_text(fingerprint + "\n", encoding="ascii")
    print(f"Built {month}: {manifest['rows']} grid-months", flush=True)
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def publish(config: dict, partitions: list[dict], build_index: bool = True) -> Path:
    description = describe(config)
    directory = runtime.ROOT / config["output_dir"]
    # Build a complete release away from any directory currently used by the API.
    temporary = Path(tempfile.mkdtemp(prefix=".release-", dir=directory))
    try:
        destination = temporary / "sample.csv"
        with destination.open("wb") as output:
            header = None
            for partition in partitions:
                source = directory / "partitions" / partition["months"][0] / "sample.csv"
                with source.open("rb") as handle:
                    current = handle.readline()
                    if header is None:
                        header = current
                        output.write(header)
                    elif current != header:
                        raise ValueError("Partition CSV schemas differ")
                    shutil.copyfileobj(handle, output, length=1024 * 1024)
        if [item["months"][0] for item in partitions] != description["months"]:
            raise ValueError("Partition months differ from requested months")
        if any(item["rows"] != description["cells_per_month"] for item in partitions):
            raise ValueError("Partition row count differs from the configured grid")
        manifest = deepcopy(partitions[0])
        manifest.update({key: description[key] for key in ["id", "bbox", "months", "cells_per_month", "rows", "grid_degrees"]})
        manifest.update(name=config["name"], sha256=digest(destination),
                        regions=config.get("regions", []), initial_bbox=config.get("initial_bbox"),
                        overview_bbox=config.get("overview_bbox"), max_region_cells=config.get("max_region_cells", 5000),
                        created_at_utc=datetime.now(timezone.utc).isoformat(),
                        build_config_sha256=config_fingerprint(config))
        manifest["quality"] = {month: stats for item in partitions for month, stats in item["quality"].items()}
        manifest["scales"] = {}
        for field in ["fishing_hours", *ENVIRONMENT_FIELDS]:
            values = [item["scales"][field] for item in partitions if field in item["scales"]]
            if values:
                manifest["scales"][field] = {"min": min(value["min"] for value in values),
                                              "max": max(value["max"] for value in values)}
        manifest["environment_fields"] = [field for field in ENVIRONMENT_FIELDS if field in manifest["scales"]]
        # Preserve every annual raw asset rather than claiming one file covers 13 years.
        gfw_assets, environment_assets = {}, {}
        for item in partitions:
            year = item["months"][0][:4]
            annual = annual_config(config, int(year))
            gfw = item["sources"]["gfw"]
            gfw_assets[year] = {key: gfw[key] for key in ["md5", "sha256", "official_checksum_verified"]}
            gfw_assets[year]["path"] = annual["gfw_zip"]
            for source in item["sources"]["environment"]:
                if source["path"] in environment_assets:
                    environment_assets[source["path"]]["months"].update(source.get("months", {}))
                else:
                    environment_assets[source["path"]] = deepcopy(source)
        manifest["sources"]["gfw"] = {
            "record_url": "https://zenodo.org/records/" + config["gfw_record"],
            "version": "3.0", "license": "CC BY-NC 4.0", "assets": list(gfw_assets.values()),
            "official_checksum_verified": all(item["official_checksum_verified"] for item in gfw_assets.values()),
            "regional_fishing_hours": sum(item["sources"]["gfw"]["regional_fishing_hours"] for item in partitions),
        }
        manifest["sources"]["environment"] = list(environment_assets.values())
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if build_index:
            dictionary = json.loads((runtime.ROOT / "docs/data_dictionary.json").read_text(encoding="utf-8"))
            database = ensure_database(temporary, dictionary)
            manifest["temperature_scales"] = {baseline: temperature_scale(database, baseline)
                                               for baseline in ["regional", "previous"]}
            (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        release = directory / ("release-" + manifest["sha256"][:16])
        if release.exists():
            existing = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
            if existing.get("sha256") != manifest["sha256"] or digest(release / "sample.csv") != manifest["sha256"]:
                raise ValueError(f"Existing release is not intact: {release}")
            if build_index and not (release / "sample.sqlite3").is_file():
                ensure_database(release, dictionary)
        else:
            temporary.rename(release)
        pointer = directory / "latest.json.tmp"
        pointer.write_text(json.dumps({"directory": release.name, "sha256": manifest["sha256"],
                                       "rows": manifest["rows"], "months": len(manifest["months"])},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
        pointer.replace(directory / "latest.json")
        return release
    finally:
        if temporary.exists():
            if temporary.resolve().parent != directory.resolve() or not temporary.name.startswith(".release-"):
                raise ValueError("Refusing to remove a staging path outside the dataset directory")
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=runtime.ROOT / "config/east_asia_2012_2024.json")
    parser.add_argument("--plan", action="store_true", help="Show the scope without downloading or writing data")
    parser.add_argument("--skip-download", action="store_true", help="Use previously downloaded, verified source assets")
    parser.add_argument("--mode", choices=["public", "toolbox"], default="toolbox")
    parser.add_argument("--no-index", action="store_true", help="Build data only; this release cannot be served until an index is built explicitly")
    args = parser.parse_args()
    config_path = args.config if args.config.is_absolute() else runtime.ROOT / args.config
    config = json.loads(config_path.read_text(encoding="utf-8"))
    description = describe(config)
    print(json.dumps(description, ensure_ascii=False, indent=2), flush=True)
    if args.plan:
        return 0
    base = Path(config["output_dir"])
    (runtime.ROOT / base).mkdir(parents=True, exist_ok=True)
    if not args.skip_download:
        fetch_effort(config)
    partitions = []
    for year in sorted(set(int(month[:4]) for month in description["months"])):
        annual = annual_config(config, year)
        if not args.skip_download:
            fetch_environment(annual, mode=args.mode)
        check_environment(annual)
        for month in description["months"]:
            if int(month[:4]) == year:
                partitions.append(build_partition(annual, month, base))
    release = publish(config, partitions, build_index=not args.no_index)
    print(json.dumps({"status": "built", "release": str(release), "rows": description["rows"],
                      "months": len(description["months"])}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
