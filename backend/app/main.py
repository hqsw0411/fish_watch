"""Read-only monthly sample API; paths do not depend on the launch directory."""
from __future__ import annotations
from contextlib import asynccontextmanager
import asyncio
import json
import math
import os
from pathlib import Path
from functools import lru_cache
from typing import Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from .store import ensure_database, select_cells
from .temperature import build_analysis, previous_calendar_month
from .paths import resolve_sample_dir
try:
    from .prediction import predict_fishing_hours
except ModuleNotFoundError as error:
    if error.name not in {"numpy", "pandas", "scipy", "sklearn", "joblib"}:
        raise
    def predict_fishing_hours(*args, **kwargs):
        raise HTTPException(status_code=503, detail="Prediction runtime dependencies are unavailable")

ROOT = Path(__file__).resolve().parents[2]
MAX_REGION_CELLS = 5000
MAX_SUPPORTED_REGION_CELLS = 40000
MAX_LEGACY_TEMPERATURE_ROWS = 250000


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(prepare_store)
    yield


app = FastAPI(title="渔场环境与努力量", version="0.3.0", lifespan=lifespan)


@lru_cache(maxsize=1)
def sample_dir() -> Path:
    # Pin the release for this worker; a supervisor restart activates a new pointer.
    return resolve_sample_dir(ROOT)


@lru_cache(maxsize=1)
def load_manifest() -> dict:
    directory = sample_dir()
    try:
        return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(status_code=503, detail="样例数据未就绪，请运行 python -m tools.data.build_sample。") from error


def prepare_store():
    dictionary = json.loads((ROOT / "docs/data_dictionary.json").read_text(encoding="utf-8"))
    return ensure_database(
        sample_dir(), dictionary,
        allow_rebuild=os.getenv("FISHERIES_ALLOW_INDEX_BUILD") == "1",
    )


def initial_bbox(manifest: dict) -> list[float]:
    # A future global coverage must not become a full-world initial request.
    west, south, east, north = manifest["bbox"]
    configured = manifest.get("initial_bbox")
    grid = float(manifest.get("grid_degrees", .1))
    if isinstance(configured, list) and len(configured) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in configured):
        w, s, e, n = configured
        if (west <= w < e <= east and south <= s < n <= north
                and math.ceil((e-w)/grid - 1e-8) * math.ceil((n-s)/grid - 1e-8) <= region_limit(manifest)):
            return configured
    longitude, latitude = (west + east) / 2, (south + north) / 2
    half_span = min(2., float(manifest.get("grid_degrees", .1)) * math.floor(math.sqrt(region_limit(manifest)))) / 2
    return [max(west, longitude - half_span), max(south, latitude - half_span),
            min(east, longitude + half_span), min(north, latitude + half_span)]


def region_limit(manifest: dict) -> int:
    value = manifest.get("max_region_cells", MAX_REGION_CELLS)
    if type(value) is not int or not 1 <= value <= MAX_SUPPORTED_REGION_CELLS:
        raise HTTPException(status_code=503, detail="Invalid published region limit")
    return value


@app.get("/api/health")
def health():
    manifest = load_manifest()
    return {"status": "ok", "sample_id": manifest["id"], "rows": manifest["rows"]}


@app.get("/api/predict")
def predict(
    lat: float = Query(description="纬度", ge=-90, le=90),
    lon: float = Query(description="经度", ge=-180, le=180),
    month: int = Query(description="月份 1-12", ge=1, le=12),
    sst_c: float = Query(description="海表温度 °C"),
    chl_mg_m3: float = Query(description="叶绿素 mg/m³"),
    npp_mg_c_m3_day: float = Query(description="净初级生产力 mg C/m³/day"),
    ssh_m: float = Query(description="海面高度 m"),
    ssha_m: float = Query(description="海面高度异常 m"),
):
    hours = predict_fishing_hours(
        lat=lat, lon=lon, month=month,
        sst_c=sst_c, chl_mg_m3=chl_mg_m3,
        npp_mg_c_m3_day=npp_mg_c_m3_day,
        ssh_m=ssh_m, ssha_m=ssha_m,
    )
    return {
        "lat": lat, "lon": lon, "month": month,
        "predicted_fishing_hours": round(hours, 2),
        "unit": "hours",
    }


@app.get("/api/catalog")
def catalog():
    manifest = load_manifest()
    return {**manifest, "initial_bbox": initial_bbox(manifest), "max_region_cells": region_limit(manifest)}


@app.get("/api/dictionary")
def dictionary():
    return json.loads((ROOT / "docs/data_dictionary.json").read_text(encoding="utf-8"))


@app.get("/api/sample")
def sample(
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    west: float | None = Query(default=None, ge=-180, le=180, allow_inf_nan=False),
    south: float | None = Query(default=None, ge=-90, le=90, allow_inf_nan=False),
    east: float | None = Query(default=None, ge=-180, le=180, allow_inf_nan=False),
    north: float | None = Query(default=None, ge=-90, le=90, allow_inf_nan=False),
):
    manifest = load_manifest()
    limit = region_limit(manifest)
    if month not in manifest["months"]:
        raise HTTPException(status_code=404, detail="月份不在当前样例范围内。")
    supplied = [west, south, east, north]
    if any(value is not None for value in supplied) and any(value is None for value in supplied):
        raise HTTPException(status_code=422, detail="区域需同时提供西界、南界、东界和北界。")
    requested = initial_bbox(manifest) if all(value is None for value in supplied) else supplied
    west, south, east, north = requested
    if west >= east or south >= north:
        raise HTTPException(status_code=422, detail="西界必须小于东界，南界必须小于北界。")
    coverage = manifest["bbox"]
    effective = [max(west, coverage[0]), max(south, coverage[1]), min(east, coverage[2]), min(north, coverage[3])]
    intersects = effective[0] < effective[2] and effective[1] < effective[3]
    if not intersects:
        cells, effective, coverage_status = [], None, "outside"
    else:
        database = sample_dir() / "sample.sqlite3"
        if not database.is_file():
            raise HTTPException(status_code=503, detail="空间索引未就绪，请重启服务完成样例建库。")
        cells = select_cells(database, month, tuple(effective), limit)
        if len(cells) > limit:
            raise HTTPException(status_code=413, detail=f"所选范围超过 {limit} 个网格，请缩小范围。")
        coverage_status = "full" if effective == requested else "partial"
    effort = [c["fishing_hours"] for c in cells if c["fishing_hours"] is not None]
    return {
        "month": month, "bbox": requested, "requested_bbox": requested,
        "effective_bbox": effective, "coverage_bbox": coverage, "coverage_status": coverage_status,
        "grid_degrees": manifest["grid_degrees"],
        "selection_rule": "grid centres inside [west,east) x [south,north)",
        "cells": cells,
        "summary": {"cells": len(cells), "observed_cells": len(effort),
                    "fishing_hours": round(sum(effort), 4) if effort else None,
                    "environment_valid_cells": {key: sum(c.get(key) is not None for c in cells)
                                                for key in manifest["environment_fields"]},
                    "energy_status": "not_available"},
        "query": {"returned_cells": len(cells), "max_cells": limit, "storage": "indexed_sqlite"},
    }


def published_temperature_scale(manifest: dict, baseline: str) -> float | None:
    value = manifest.get("temperature_scales", {}).get(baseline)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise HTTPException(status_code=503, detail="Invalid published temperature scale")
    return float(value)


@lru_cache(maxsize=2)
def temperature_monthly(month: str, baseline: str):
    manifest = load_manifest()
    database = sample_dir() / "sample.sqlite3"
    if not database.is_file():
        raise HTTPException(status_code=503, detail="空间索引未就绪，请重启服务。")
    if published_temperature_scale(manifest, baseline) is not None:
        months = [month]
        previous = previous_calendar_month(month)
        if baseline == "previous" and previous in manifest["months"]:
            months.append(previous)
    else:
        if manifest["rows"] > MAX_LEGACY_TEMPERATURE_ROWS:
            raise HTTPException(status_code=503, detail="Large releases require precomputed temperature scales")
        months = manifest["months"]
    limit = region_limit(manifest)
    result = {key: select_cells(database, key, tuple(manifest["bbox"]), limit) for key in months}
    if any(len(cells) > limit for cells in result.values()):
        raise HTTPException(status_code=413, detail="温差参考域超过已发布的网格上限。")
    return result


@app.get("/api/temperature")
def temperature(
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    baseline: Literal["regional", "previous"] = "regional",
    percentile: int = Query(default=75, ge=50, le=95),
    west: float | None = Query(default=None, ge=-180, le=180, allow_inf_nan=False),
    south: float | None = Query(default=None, ge=-90, le=90, allow_inf_nan=False),
    east: float | None = Query(default=None, ge=-180, le=180, allow_inf_nan=False),
    north: float | None = Query(default=None, ge=-90, le=90, allow_inf_nan=False),
):
    manifest = load_manifest()
    if month not in manifest["months"]:
        raise HTTPException(status_code=404, detail="月份不在当前样例范围内。")
    supplied = [west, south, east, north]
    if any(value is not None for value in supplied) and any(value is None for value in supplied):
        raise HTTPException(status_code=422, detail="区域需同时提供西界、南界、东界和北界。")
    bbox = manifest["bbox"] if all(value is None for value in supplied) else supplied
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise HTTPException(status_code=422, detail="西界必须小于东界，南界必须小于北界。")
    return build_analysis(month, temperature_monthly(month, baseline), manifest, bbox, baseline, percentile,
                          fixed_scale=published_temperature_scale(manifest, baseline))


# Vite proxies /api during development. The built UI uses the same origin.
DIST = ROOT / "frontend/dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
