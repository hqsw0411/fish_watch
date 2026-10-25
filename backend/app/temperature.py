"""Reproducible SST differences and descriptive effort checks; no fish forecast."""
from __future__ import annotations

import math
from statistics import fmean


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def quantile(values: list[float], probability: float) -> float | None:
    """Linear interpolation at (n-1)*p, including ties."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def previous_calendar_month(month: str) -> str:
    year, numeric = map(int, month.split("-"))
    return f"{year - (numeric == 1):04d}-{12 if numeric == 1 else numeric - 1:02d}"


def derive(cells: list[dict], previous: list[dict], baseline: str) -> tuple[list[dict], dict]:
    valid = [cell["sst_c"] for cell in cells if finite(cell.get("sst_c"))]
    regional_mean = fmean(valid) if valid else None
    prior = {cell["cell_id"]: cell.get("sst_c") for cell in previous}
    result = []
    for cell in cells:
        reference = regional_mean if baseline == "regional" else prior.get(cell["cell_id"])
        value = cell.get("sst_c")
        delta = value - reference if finite(value) and finite(reference) else None
        result.append({**cell, "reference_sst_c": reference, "delta_sst_c": delta,
                       "abs_delta_sst_c": abs(delta) if delta is not None else None,
                       "delta_status": "available" if delta is not None else
                       "sst_missing" if not finite(value) else "reference_missing"})
    return result, {"regional_mean_sst_c": regional_mean, "valid_sst_cells": len(valid),
                    "valid_delta_cells": sum(cell["delta_sst_c"] is not None for cell in result)}


def pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    mx, my = fmean(x for x, _ in pairs), fmean(y for _, y in pairs)
    xx = sum((x - mx) ** 2 for x, _ in pairs)
    yy = sum((y - my) ** 2 for _, y in pairs)
    if xx <= 1e-20 or yy <= 1e-20:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(xx * yy)


def summarize(cells: list[dict]) -> dict:
    valid = [cell for cell in cells if finite(cell.get("delta_sst_c"))]
    paired = [cell for cell in valid if finite(cell.get("fishing_hours")) and cell["fishing_hours"] >= 0]
    candidate = [cell for cell in valid if cell["candidate"]]
    candidate_paired = [cell for cell in paired if cell["candidate"]]
    hours = sum(cell["fishing_hours"] for cell in paired)
    candidate_hours = sum(cell["fishing_hours"] for cell in candidate_paired)
    cell_share = len(candidate_paired) / len(paired) if paired else None
    hour_share = candidate_hours / hours if hours > 0 else None
    return {"cells": len(cells), "valid_delta_cells": len(valid), "candidate_cells": len(candidate),
            "paired_cells": len(paired), "candidate_paired_cells": len(candidate_paired),
            "known_zero_cells": sum(cell["fishing_hours"] == 0 for cell in paired),
            "missing_effort_cells": sum(not finite(cell.get("fishing_hours")) for cell in cells),
            "paired_fishing_hours": hours if paired else None,
            "candidate_fishing_hours": candidate_hours if candidate_paired else None,
            "candidate_known_cell_share": cell_share, "candidate_effort_share": hour_share,
            "effort_concentration_ratio": hour_share / cell_share if hour_share is not None and cell_share else None,
            "pearson_delta_log_effort": pearson([(cell["delta_sst_c"], math.log1p(cell["fishing_hours"])) for cell in paired]),
            "pearson_abs_delta_log_effort": pearson([(cell["abs_delta_sst_c"], math.log1p(cell["fishing_hours"])) for cell in paired])}


def build_analysis(month: str, monthly: dict[str, list[dict]], manifest: dict,
                   bbox: list[float], baseline: str = "regional", percentile: int = 75,
                   fixed_scale: float | None = None) -> dict:
    """Derive on the fixed full domain BEFORE selecting the ROI; no hidden rebasing."""
    if baseline not in {"regional", "previous"}:
        raise ValueError("Unknown baseline")
    prior_month = previous_calendar_month(month)
    derived, reference = derive(monthly[month], monthly.get(prior_month, []), baseline)
    threshold = quantile([cell["abs_delta_sst_c"] for cell in derived if cell["abs_delta_sst_c"] is not None], percentile / 100)
    for cell in derived:
        value = cell["abs_delta_sst_c"]
        # A constant field has no difference candidate even when its quantile is zero.
        cell["candidate"] = value is not None and threshold is not None and value > 1e-12 and value >= threshold
    west, south, east, north = bbox
    selected = [cell for cell in derived if west <= cell["longitude"] < east and south <= cell["latitude"] < north]
    coverage = manifest["bbox"]
    effective = [max(west, coverage[0]), max(south, coverage[1]), min(east, coverage[2]), min(north, coverage[3])]
    intersects = effective[0] < effective[2] and effective[1] < effective[3]
    # Both baseline modes get their own symmetric, fixed scale across all delivered months.
    if fixed_scale is None:
        scale_values = [abs(cell["delta_sst_c"]) for key in monthly
                        for cell in derive(monthly[key], monthly.get(previous_calendar_month(key), []), baseline)[0]
                        if cell["delta_sst_c"] is not None]
        scale = max(scale_values, default=0.) or 1.
    else:
        scale = fixed_scale or 1.
    return {"month": month, "baseline": baseline, "percentile": percentile,
            "requested_bbox": bbox, "effective_bbox": effective if intersects else None,
            "coverage_status": "outside" if not intersects else "full" if effective == bbox else "partial",
            "grid_degrees": manifest["grid_degrees"], "cells": selected,
            "reference": {**reference, "bbox": coverage, "previous_month": prior_month if baseline == "previous" else None,
                          "label": "同月完整数据域有效网格的等权均值" if baseline == "regional" else "同网格上一日历月海温",
                          "threshold_abs_delta_c": threshold, "quantile_method": "linear (n-1)*p"},
            "scale": {"min": -scale, "max": scale}, "summary": summarize(selected),
            "source": {"sample_id": manifest["id"], "sha256": manifest["sha256"],
                       "sst": "Copernicus GLORYS thetao, surface potential temperature proxy (~0.494m)",
                       "effort": "GFW v3 apparent fishing hours; known zeros included, missing excluded"},
            "interpretation": "温差候选区为特征探索；GFW重合与相关是样本内描述，不是鱼类适宜性或预测验证。"}
