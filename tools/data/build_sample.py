"""Create a reproducible real-data monthly grid, with no imputed effort or energy."""
from __future__ import annotations
import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from datetime import datetime, timezone

from tools import paths as runtime
import numpy as np
import pandas as pd
import xarray as xr

ENVIRONMENT_FIELDS = ["sst_c", "chl_mg_m3", "npp_mg_c_m3_day", "ssh_m", "ssha_m"]
EXPECTED_UNITS = {"sst_c": {"degrees_C", "degree_Celsius"}, "chl_mg_m3": {"mg m-3"},
                  "npp_mg_c_m3_day": {"mg m-3 day-1"}, "ssh_m": {"m"}, "ssha_m": {"m"}}


def make_grid(config: dict) -> pd.DataFrame:
    west, south, east, north = config["bbox"]
    step = float(config["grid_degrees"])
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90 and step > 0):
        raise ValueError("Invalid bbox/resolution; dateline crossing is out of pilot scope")
    for value in config["bbox"]:
        if abs(value / step - round(value / step)) > 1e-7:
            raise ValueError("BBox must align with the target grid origin")
    latitudes = np.round(np.arange(south, north - step / 2, step), 6)
    longitudes = np.round(np.arange(west, east - step / 2, step), 6)
    rows = []
    for month in pd.period_range(config["start_month"], config["end_month"], freq="M"):
        for lat in latitudes:
            for lon in longitudes:
                rows.append({"month": str(month), "cell_id": f"{lon:.4f}_{lat:.4f}",
                             "cell_ll_lon": lon, "cell_ll_lat": lat,
                             "longitude": round(lon + step / 2, 6),
                             "latitude": round(lat + step / 2, 6)})
    if not rows:
        raise ValueError("Empty month range/grid")
    return pd.DataFrame(rows)


def effort_from_zip(config: dict) -> tuple[pd.DataFrame, dict]:
    months = [str(p) for p in pd.period_range(config["start_month"], config["end_month"], freq="M")]
    west, south, east, north = config["bbox"]
    archive = runtime.ROOT / config["gfw_zip"]
    chunks, audit = [], {}
    with zipfile.ZipFile(archive) as bundle:
        for month in months:
            name = f"fleet-monthly-csvs-10-v3-{month}-01.csv"
            if name not in bundle.namelist():
                raise ValueError(f"Missing GFW month: {month}")
            total, matched = 0, 0
            month_chunks = []
            with bundle.open(name) as handle:
                for frame in pd.read_csv(handle, chunksize=100000,
                                         usecols=["date", "cell_ll_lon", "cell_ll_lat", "hours", "fishing_hours", "geartype"]):
                    total += len(frame)
                    frame = frame.loc[frame.cell_ll_lon.ge(west) & frame.cell_ll_lon.lt(east)
                                      & frame.cell_ll_lat.ge(south) & frame.cell_ll_lat.lt(north)].copy()
                    matched += len(frame)
                    if frame.empty:
                        continue
                    frame[["cell_ll_lon", "cell_ll_lat"]] = frame[["cell_ll_lon", "cell_ll_lat"]].astype(float)
                    if not frame["date"].str.startswith(month).all():
                        raise ValueError(f"Date mismatch inside {name}")
                    numeric = frame[["hours", "fishing_hours"]]
                    if not np.isfinite(numeric.to_numpy()).all() or (numeric < 0).any().any():
                        raise ValueError("Invalid effort values")
                    if (frame.fishing_hours > frame.hours + 1e-6).any():
                        raise ValueError("Fishing hours exceed vessel presence hours")
                    step = config["grid_degrees"]
                    for coordinate in ["cell_ll_lon", "cell_ll_lat"]:
                        if not np.allclose(frame[coordinate] / step, np.round(frame[coordinate] / step), atol=1e-7, rtol=0):
                            raise ValueError("Source cells do not align to configured grid")
                    frame["month"] = month
                    frame["source_row_count"] = 1
                    month_chunks.append(frame)
            audit[month] = {"member": name, "raw_rows": total, "regional_rows": matched}
            if month_chunks:
                detail = pd.concat(month_chunks, ignore_index=True)
                aggregates = detail.groupby(["month", "cell_ll_lon", "cell_ll_lat"], as_index=False).agg(
                    hours=("hours", "sum"), fishing_hours=("fishing_hours", "sum"),
                    source_row_count=("source_row_count", "sum"),
                    gear_types=("geartype", lambda values: ";".join(sorted(set(values.dropna())))))
                chunks.append(aggregates)
    if not chunks:
        raise ValueError("No GFW data in the configured region/time")
    aggregates = pd.concat(chunks, ignore_index=True)
    audit["regional_fishing_hours"] = float(aggregates.fishing_hours.sum())
    audit["md5"] = hashlib.md5(archive.read_bytes()).hexdigest()
    audit["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
    # Verify the existing archive against the official record when available.
    record = runtime.ROOT / config.get("gfw_metadata", "data/metadata/zenodo_14982712.record.json")
    if record.is_file():
        files = json.loads(record.read_text(encoding="utf-8"))["files"]
        official = next(f for f in files if f["key"] == archive.name)
        if official["checksum"] != "md5:" + audit["md5"]:
            raise ValueError("GFW archive checksum differs from official Zenodo metadata")
        audit["official_checksum_verified"] = True
    else:
        audit["official_checksum_verified"] = False
    return aggregates, audit


def align_environment(frame: pd.DataFrame, source: dict, root: Path | None = None) -> dict:
    """Bounded nearest-cell surface values; monthly matching prevents cross-month leakage."""
    root = runtime.ROOT if root is None else root
    file = root / source["path"]
    if not file.is_file():
        return {"dataset_id": source["dataset_id"], "status": "missing_file", "path": source["path"]}
    result = {"dataset_id": source["dataset_id"], "path": source["path"],
              "sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "months": {}}
    metadata_file = file.with_suffix(".metadata.json")
    if metadata_file.is_file():
        download = json.loads(metadata_file.read_text(encoding="utf-8"))
        if download.get("sha256") != result["sha256"]:
            raise ValueError(f"Environment source checksum differs from download audit: {file}")
        result["download"] = {key: download[key] for key in
                              ["dataset_version", "asset_uri", "mode", "bbox_with_margin", "start", "end", "sha256"]
                              if key in download}
    with xr.open_dataset(file) as ds:
        periods = pd.DatetimeIndex(ds.time.values).to_period("M")
        xs, ys = np.sort(ds.longitude.values), np.sort(ds.latitude.values)
        if len(xs) < 2 or len(ys) < 2:
            raise ValueError("Source grid needs at least two coordinates per axis")
        dx, dy = float(np.median(np.diff(xs))), float(np.median(np.diff(ys)))
        result["native_grid_degrees"] = [dx, dy]
        result["source_time_values"] = [pd.Timestamp(t).isoformat() for t in ds.time.values]
        result["global_attributes"] = dict(ds.attrs)
        result["surface_coordinates"] = {k: ds[k].values.tolist() for k in ("depth", "elevation", "deptht") if k in ds.coords}
        result["variables"] = {v: dict(ds[v].attrs) for v in source["variables"]}
        for month, group in frame.groupby("month"):
            times = np.flatnonzero(periods == pd.Period(month, freq="M"))
            result["months"][month] = {"source_records": len(times)}
            if not len(times):
                continue
            selected = ds.isel(time=times).mean("time", skipna=True)
            for vertical in ("depth", "elevation", "deptht"):
                if vertical in selected.dims:
                    selected = selected.isel({vertical: int(abs(selected[vertical]).argmin(dim=vertical).item())})
            in_domain = (group.longitude.between(xs[0] - dx / 2, xs[-1] + dx / 2)
                         & group.latitude.between(ys[0] - dy / 2, ys[-1] + dy / 2)).to_numpy()
            for variable, field in source["variables"].items():
                units = ds[variable].attrs.get("units")
                if units not in EXPECTED_UNITS[field]:
                    raise ValueError(f"Unrecognised unit for {field}: {units!r}")
                values = selected[variable].sel(
                    longitude=xr.DataArray(group.longitude.to_numpy(), dims="cell"),
                    latitude=xr.DataArray(group.latitude.to_numpy(), dims="cell"),
                    method="nearest").values.astype(float)
                values[~in_domain] = np.nan
                frame.loc[group.index, field] = values
        result["status"] = "aligned"
    return result


def build(config: dict, require_complete: bool = False) -> dict:
    grid = make_grid(config)
    effort, effort_audit = effort_from_zip(config)
    frame = grid.merge(effort, how="left", on=["month", "cell_ll_lon", "cell_ll_lat"], validate="one_to_one")
    frame["source_row_count"] = frame.source_row_count.fillna(0).astype(int)
    frame["effort_status"] = np.where(frame.source_row_count.eq(0), "no_record",
                                      np.where(frame.fishing_hours.eq(0), "reported_zero", "observed"))
    frame["energy_kwh"] = np.nan
    frame["energy_status"] = "not_available"
    for field in ENVIRONMENT_FIELDS:
        frame[field] = np.nan
    sources = [align_environment(frame, source) for source in config["environment_sources"]]
    requested_fields = [field for source in config["environment_sources"] for field in source["variables"].values()]
    frame["environment_status"] = np.where(frame[requested_fields].notna().all(axis=1), "available", "partial_or_missing")
    if require_complete and not frame[requested_fields].notna().all().all():
        raise ValueError("Incomplete environment sample: missing region/month values")
    if not np.isclose(frame.fishing_hours.sum(), effort_audit["regional_fishing_hours"]):
        raise ValueError("Effort not conserved during grid alignment")
    directory = runtime.ROOT / config["output_dir"]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "sample.csv"
    frame.to_csv(destination, index=False, float_format="%.8f", encoding="utf-8", lineterminator="\n")
    months = sorted(frame.month.unique())
    fields = [f for f in ENVIRONMENT_FIELDS if frame[f].notna().any()]
    manifest = {
        "id": config["id"], "name": config["name"], "bbox": config["bbox"],
        "grid_degrees": config["grid_degrees"], "crs": config["crs"], "months": months,
        "rows": len(frame), "cells_per_month": int(frame.groupby("month").size().iloc[0]),
        "environment_fields": fields, "energy_available": False,
        "regions": config.get("regions", []),
        "initial_bbox": config.get("initial_bbox"), "overview_bbox": config.get("overview_bbox"),
        "max_region_cells": config.get("max_region_cells", 5000),
        "fronts": {"available": False, "reason": "当前未提供独立的海洋锋面折线数据"},
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "spatial_method": "nearest source-cell centre; no extrapolation beyond source-cell domain",
        "time_method": "same calendar month; arithmetic mean of source records; surface model level",
        "quality": {m: {"rows": len(g), "observed_effort_cells": int(g.fishing_hours.notna().sum()),
                        "fishing_hours": float(g.fishing_hours.sum()),
                        "valid_environment": {f: int(g[f].notna().sum()) for f in ENVIRONMENT_FIELDS}}
                    for m, g in frame.groupby("month")},
        "scales": {f: {"min": float(frame[f].min()), "max": float(frame[f].max())}
                   for f in ["fishing_hours", *fields] if frame[f].notna().any()},
        "sources": {"gfw": {"record_url": "https://zenodo.org/records/14982712", "version": "3.0", "license": "CC BY-NC 4.0", **effort_audit},
                    "environment": sources,
                    "basemap": {"name": "Natural Earth 1:110m land", "license": "public domain",
                                "url": "https://github.com/nvkelso/natural-earth-vector"}},
        "limitations": ["AIS无记录不等于零捕捞或无鱼；捕捞时长不是渔获量。",
                        "月度舰队数据未提供能耗或逐船功率，energy_kwh保持空值。",
                        "表层thetao作为海温代理；nppv为体积生产率，不是水柱积分NPP。",
                        "zos为SSH；ssha_m单独来自DUACS的sla产品，其参考期以源属性为准。",
                        "0.25°生地化值映射到0.1°不会提高原始信息分辨率。"],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=runtime.json_default), encoding="utf-8")
    print(json.dumps({"sample": str(destination), "rows": len(frame), "months": months,
                      "quality": manifest["quality"]}, ensure_ascii=False, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=runtime.ROOT / "config/patagonia_2012_h1.json")
    parser.add_argument("--require-complete-env", action="store_true")
    args = parser.parse_args()
    build(json.loads(args.config.read_text(encoding="utf-8")), args.require_complete_env)
