import numpy as np
import pandas as pd

# 文档规定的首版特征。环境因子目前CSV里没有，代码里做容错：
# 列不存在就跳过，等后续A join好环境数据再自动用上。
ENV_FEATURES = ["sst_c", "chl_mg_m3", "npp_mg_c_m3_day", "ssh_m", "ssha_m"]
SPATIAL_FEATURES = ["cell_ll_lat", "cell_ll_lon"]
TIME_FEATURES = ["month_sin", "month_cos"]

TARGET = "fishing_hours"
HOURS = "hours"

# 文档明确禁用：hours是渔船活动总时长（含非捕捞），geartype是作业类型，
# 直接用会造成目标泄漏或引入不可控的渔船选择偏差。
FORBIDDEN = ["hours", "source_row_count", "geartype", "flag", "mmsi_present"]


def assign_status(df: pd.DataFrame) -> pd.Series:
    """
    fishing_hours > 0         -> observed
    fishing_hours == 0 且 hours > 0 -> reported_zero
    （no_record 需要完整网格月历才能判定，CSV 里不出现的行才算，
      这里打不出来，留给A建库时补。）
    """
    if (df[TARGET].dropna() < 0).any():
        raise ValueError("Negative fishing hours are invalid")
    s = pd.Series("no_record", index=df.index)
    s[df[TARGET] == 0] = "reported_zero"
    s[df[TARGET] > 0] = "observed"
    return s


def month_cyclic(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    m = df["month"].astype(float)
    df["month_sin"] = np.sin(2 * np.pi * m / 12)
    df["month_cos"] = np.cos(2 * np.pi * m / 12)
    return df


def build_feature_list(df: pd.DataFrame) -> list:
    """实际可用的特征 = 空间 + 时间 + 环境（缺啥补啥）。"""
    feats = list(SPATIAL_FEATURES) + list(TIME_FEATURES)
    for c in ENV_FEATURES:
        if c in df.columns:
            feats.append(c)
    return feats


def log1p_target(y: np.ndarray) -> np.ndarray:
    return np.log1p(np.clip(y, a_min=0, a_max=None))


def expm1_target(y_log: np.ndarray) -> np.ndarray:
    return np.expm1(y_log)


def corr_report(df: pd.DataFrame, features: list) -> dict:
    sub = df[features].dropna()
    p = sub.corr(method="pearson").round(4)
    s = sub.corr(method="spearman").round(4)

    pairs = []
    for i in range(len(features)):
        for j in range(i + 1, len(features)):
            r = p.iloc[i, j]
            if abs(r) >= 0.8:
                pairs.append({
                    "a": features[i], "b": features[j],
                    "pearson": float(r),
                    "spearman": float(s.iloc[i, j]),
                })
    return {
        "pearson": p.to_dict(),
        "spearman": s.to_dict(),
        "high_corr_pairs": pairs,
        "note": "相关性仅用于共线性排查，不解释为因果。",
    }
