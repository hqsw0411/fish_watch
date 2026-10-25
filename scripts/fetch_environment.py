"""Download only the configured region/time, from official catalogue-listed assets.

Public mode uses the openly readable ARCO Zarr service advertised by Copernicus.
It never bypasses access denial. Toolbox mode uses the official account/login.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

import runtime
import pandas as pd
import xarray as xr


def write_audit(target: Path, audit: dict) -> None:
    audit["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    with xr.open_dataset(target) as ds:
        audit["sizes"] = dict(ds.sizes)
        audit["surface_coordinates"] = {k: ds[k].values.tolist() for k in ("depth", "elevation", "deptht") if k in ds.coords}
        audit["time_values"] = [pd.Timestamp(t).isoformat() for t in ds.time.values]
        audit["global_attributes"] = dict(ds.attrs)
        audit["variable_attributes"] = {v: dict(ds[v].attrs) for v in audit["variables"]}
    target.with_suffix(".metadata.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=runtime.json_default), encoding="utf-8")


def fetch(config: dict, mode: str, refresh: bool = False) -> None:
    import copernicusmarine
    import fsspec
    west, south, east, north = config["bbox"]
    for source in config["environment_sources"]:
        target = runtime.ROOT / source["path"]
        start = config["start_month"] + "-01"
        end = str(pd.Period(config["end_month"], freq="M").end_time)
        audit = {"dataset_id": source["dataset_id"], "mode": mode,
                 "bbox_with_margin": [west - .5, south - .5, east + .5, north + .5],
                 "start": start, "end": end, "variables": list(source["variables"])}
        metadata_path = target.with_suffix(".metadata.json")
        if target.exists() and metadata_path.exists() and not refresh:
            cached = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (all(cached.get(key) == audit[key] for key in ["dataset_id", "bbox_with_margin", "start", "end", "variables"])
                    and cached.get("sha256") == hashlib.sha256(target.read_bytes()).hexdigest()):
                write_audit(target, cached)
                print(f"Verified cache: {target}")
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "toolbox":
            result = copernicusmarine.subset(
                dataset_id=source["dataset_id"], variables=list(source["variables"]),
                minimum_longitude=west - .5, maximum_longitude=east + .5,
                minimum_latitude=south - .5, maximum_latitude=north + .5,
                start_datetime=start, end_datetime=end, minimum_depth=0,
                maximum_depth=1, output_directory=str(target.parent),
                output_filename=target.name, overwrite=True,
            )
            audit["toolbox_result"] = str(result)
        else:
            catalogue = copernicusmarine.describe(dataset_id=source["dataset_id"],
                                                  disable_progress_bar=True, raise_on_error=True)
            raw = catalogue.model_dump(mode="json")
            (runtime.ROOT / f"data/metadata/cmems_{source['id']}.catalog.json").write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            datasets = [d for p in raw["products"] for d in p["datasets"]
                        if d["dataset_id"] == source["dataset_id"]]
            if len(datasets) != 1:
                raise ValueError("Dataset not unique in official catalogue")
            version = datasets[0]["versions"][0]
            services = [s for part in version["parts"] for s in part["services"]
                        if s["service_short_name"] == "timeseries" and s["service_format"] == "zarr"]
            if len(services) != 1:
                raise ValueError("No unambiguous official ARCO time-series service")
            uri = services[0]["uri"]
            # A public asset must return its own metadata without credentials.
            # A 401/403 propagates; callers may switch to official toolbox mode.
            with urlopen(uri + "/.zmetadata", timeout=40) as response:
                json.load(response)
            audit.update({"asset_uri": uri, "dataset_version": version["label"]})
            # Official assets use Zarr v2; do not probe a nonexistent v3 zarr.json.
            with xr.open_zarr(fsspec.get_mapper(uri), consolidated=True, zarr_format=2) as remote:
                subset = remote[list(source["variables"])].sel(
                    time=slice(start, end), longitude=slice(west - .5, east + .5),
                    latitude=slice(south - .5, north + .5))
                for vertical in ("depth", "elevation", "deptht"):
                    if vertical in subset.dims:
                        surface_index = int(abs(subset[vertical]).argmin(dim=vertical).item())
                        subset = subset.isel({vertical: slice(surface_index, surface_index + 1)})
                if subset.sizes.get("time", 0) == 0:
                    raise ValueError("Source has no records for the requested period")
                print(source["id"], "loading regional subset", dict(subset.sizes), flush=True)
                subset = subset.load()
                temporary = target.with_suffix(".download.nc")
                subset.to_netcdf(temporary, engine="h5netcdf")
                temporary.replace(target)
        write_audit(target, audit)
        print("Saved", target, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=runtime.ROOT / "config/sample_region.json")
    parser.add_argument("--mode", choices=["public", "toolbox"], default="public")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    fetch(json.loads(args.config.read_text(encoding="utf-8")), args.mode, args.refresh)
