"""随机森林渔场预测：加载模型，接收特征，返回 fishing_hours。"""
from __future__ import annotations
import math
import os
from functools import lru_cache
from pathlib import Path
import numpy as np
import pandas as pd
from joblib import load
from fastapi import HTTPException
from .paths import anchored_path

ROOT = Path(__file__).resolve().parents[2]

# Keep weights outside versioned source; select a reviewed artifact explicitly.
DEFAULT_MODEL_DIR = ROOT / "model-artifacts" / "current"

FEATURE_ORDER = [
    "cell_ll_lat", "cell_ll_lon",
    "month_sin", "month_cos",
    "sst_c", "chl_mg_m3", "npp_mg_c_m3_day", "ssh_m", "ssha_m",
]


@lru_cache(maxsize=1)
def get_model():
    model_dir = anchored_path(os.getenv("FISHERIES_MODEL_DIR", str(DEFAULT_MODEL_DIR)), ROOT)
    model_path = model_dir / "model.joblib"
    if not model_path.is_file():
        raise HTTPException(status_code=503, detail="Prediction model is not configured")
    return load(model_path)


def month_to_cyclic(month: int) -> tuple[float, float]:
    rad = 2 * math.pi * month / 12
    return math.sin(rad), math.cos(rad)


def predict_fishing_hours(
    lat: float,
    lon: float,
    month: int,
    sst_c: float,
    chl_mg_m3: float,
    npp_mg_c_m3_day: float,
    ssh_m: float,
    ssha_m: float,
) -> float:
    """输入单条样本，返回预测的 fishing_hours（原始小时尺度）。"""
    m_sin, m_cos = month_to_cyclic(month)
    values = dict(zip(FEATURE_ORDER, [lat, lon, m_sin, m_cos, sst_c, chl_mg_m3,
                                    npp_mg_c_m3_day, ssh_m, ssha_m]))
    if not all(math.isfinite(value) for value in values.values()):
        raise HTTPException(status_code=422, detail="Prediction features must be finite")
    model = get_model()
    order = list(getattr(model, "feature_names_in_", FEATURE_ORDER))
    if not order or any(name not in values for name in order):
        raise HTTPException(status_code=503, detail="Model feature schema is incompatible with this API")
    # Training saves the raw regressor in log1p space. Honor its recorded column
    # names (including models trained without environmental fields) and invert once.
    rows = [[values[name] for name in order]]
    X = pd.DataFrame(rows, columns=order) if hasattr(model, "feature_names_in_") else np.array(rows, dtype=float)
    y_log = model.predict(X)
    y = np.expm1(y_log)
    if not math.isfinite(float(y[0])):
        raise HTTPException(status_code=503, detail="Model returned a non-finite prediction")
    return float(y[0])
