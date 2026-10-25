import argparse
import datetime
import hashlib
import json
import os
import glob
import sys

# 保证从任意目录运行都能 import 到项目根下的 models 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from models.features import (
    TARGET, assign_status, build_feature_list, month_cyclic,
    corr_report,
)
from models.random_forest import RFFishing

RUNS_DIR = os.environ.get("FISHERIES_MODEL_RUNS_DIR", os.path.join(ROOT, "model-artifacts", "runs"))
DEFAULT_CSV_DIR = os.path.join(ROOT, "fleet-monthly-csvs-10-v3-2012")
DEFAULT_DB_PATH = os.environ.get("FISHERIES_RESEARCH_DB", os.path.join(ROOT, "data", "database", "fisheries_forecast.sqlite3"))
DEFAULT_DATASET_ID = "gfw_v3.0"


def load_csvs(csv_dir: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    if not files:
        raise FileNotFoundError(f"目录下没有csv: {csv_dir}")
    parts = [pd.read_csv(f) for f in files]
    df = pd.concat(parts, ignore_index=True)
    if "year" not in df.columns or "month" not in df.columns:
        if "date" not in df.columns:
            raise ValueError("CSV requires year/month columns or a parseable date column")
        dates = pd.to_datetime(df["date"], errors="raise")
        df["year"] = dates.dt.year
        df["month"] = dates.dt.month
    df["effort_status"] = assign_status(df)
    df = df[df["effort_status"].isin(["observed", "reported_zero"])].copy()
    df = month_cyclic(df)
    return df


def load_from_db(db_path: str, dataset_id: str) -> pd.DataFrame:
    """
    从 SQLite 读训练数据（对接 fisheries_forecast.sqlite3）。
    三表 JOIN + 环境因子长表透视：
      effort_cell_period(每网格每月聚合捕捞量)
      JOIN grid_cell(经纬度)
      JOIN time_dimension(年/月)
      LEFT JOIN environment_observation(按 cell+time 透视出 5 个环境变量)
    只保留 observed + reported_zero，剔除 no_record。
    """
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        df = pd.read_sql_query(
            """
            SELECT e.cell_id, g.ll_lat AS cell_ll_lat, g.ll_lon AS cell_ll_lon,
                   e.fishing_hours, e.hours, e.effort_status,
                   t.year, t.month,
                   MAX(CASE WHEN env.variable_id = 'var_sst_c' THEN env.value END) AS sst_c,
                   MAX(CASE WHEN env.variable_id = 'var_chl_mg_m3' THEN env.value END) AS chl_mg_m3,
                   MAX(CASE WHEN env.variable_id = 'var_npp_mg_c_m3_day' THEN env.value END) AS npp_mg_c_m3_day,
                   MAX(CASE WHEN env.variable_id = 'var_ssh_m' THEN env.value END) AS ssh_m,
                   MAX(CASE WHEN env.variable_id = 'var_ssha_m' THEN env.value END) AS ssha_m
            FROM effort_cell_period e
            JOIN grid_cell g ON e.cell_id = g.cell_id
            JOIN time_dimension t ON e.time_id = t.time_id
            LEFT JOIN environment_observation env
              ON e.cell_id = env.cell_id AND e.time_id = env.time_id
            WHERE e.dataset_version_id = :ds
            GROUP BY e.cell_id, e.time_id, e.fishing_hours, e.hours, e.effort_status,
                     t.year, t.month, g.ll_lat, g.ll_lon
            """,
            engine,
            params={"ds": dataset_id},
        )
    finally:
        engine.dispose()

    if df.empty:
        raise ValueError(f"dataset_id={dataset_id} 在 {db_path} 里没有数据")

    if "effort_status" in df.columns:
        df = df[df["effort_status"].isin(["observed", "reported_zero"])].copy()
    if "effort_status" not in df.columns:
        df["effort_status"] = assign_status(df)
    if "month" not in df.columns and "date" in df.columns:
        df["month"] = pd.to_datetime(df["date"]).dt.month
    df = month_cyclic(df)
    return df


def metrics(y_true, y_pred) -> dict:
    r2 = r2_score(y_true, y_pred)
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae = mean_absolute_error(y_true, y_pred)
    # 预测-实测 线性拟合：y_pred = slope * y_true + intercept
    slope, intercept = np.polyfit(y_true, y_pred, deg=1)
    return {
        "r2": round(float(r2), 4),
        "rmse": round(rmse, 4),
        "mae": round(float(mae), 4),
        "slope": round(float(slope), 4),
        "intercept": round(float(intercept), 4),
        "n": int(len(y_true)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["csv", "sqlite"], default="sqlite",
                    help="数据源：sqlite 从数据库读（默认），csv 直读文件")
    ap.add_argument("--csv_dir", default=DEFAULT_CSV_DIR)
    ap.add_argument("--db_path", default=DEFAULT_DB_PATH)
    ap.add_argument("--dataset_id", default=DEFAULT_DATASET_ID)
    ap.add_argument("--split_mode", choices=["auto", "manual", "none"], default="auto",
                    help="auto=按时间排序最后N个时间片测试(默认，数据增长自动扩展)；manual=指定月份；none=全量训练")
    ap.add_argument("--test_periods", type=int, default=2,
                    help="auto模式：取最后N个(年,月)时间片做测试")
    ap.add_argument("--train_months", type=int, nargs="+", default=[1, 2, 3, 4],
                    help="manual模式：训练月份")
    ap.add_argument("--test_months", type=int, nargs="+", default=[5, 6],
                    help="manual模式：测试月份")
    ap.add_argument("--n_estimators", type=int, default=150)
    ap.add_argument("--max_features", type=float, default=0.7)
    ap.add_argument("--min_samples_leaf", type=int, default=10)
    ap.add_argument("--max_depth", type=int, default=20,
                    help="限制单棵树深度，控制模型体积与过拟合；None表示不限")
    ap.add_argument("--random_state", type=int, default=42)
    args = ap.parse_args()

    if args.source == "sqlite":
        df = load_from_db(args.db_path, args.dataset_id)
        data_source = os.path.basename(args.db_path)
    else:
        df = load_csvs(args.csv_dir)
        data_source = os.path.basename(args.csv_dir)

    # 时间片 = year*12+month，跨年也能按时间排序；数据增长后自动覆盖全部历史
    df["period"] = df["year"].astype(int) * 12 + df["month"].astype(int)
    periods = sorted(df["period"].unique().tolist())
    if args.split_mode == "none":
        train = df.copy()
        test = df.iloc[0:0].copy()
    elif args.split_mode == "manual":
        train = df[df["month"].isin(args.train_months)].copy()
        test = df[df["month"].isin(args.test_months)].copy()
    else:  # auto
        test_ps = set(periods[-args.test_periods:])
        train = df[~df["period"].isin(test_ps)].copy()
        test = df[df["period"].isin(test_ps)].copy()

    feats = build_feature_list(train)
    X_tr, y_tr = train[feats], train[TARGET].values
    X_te, y_te = test[feats], test[TARGET].values

    print(f"训练 {len(train)} 行 / 测试 {len(test)} 行 / 特征 {feats}")

    model = RFFishing(
        n_estimators=args.n_estimators,
        max_features=args.max_features,
        min_samples_leaf=args.min_samples_leaf,
        max_depth=args.max_depth,
        random_state=args.random_state,
    )
    model.fit(X_tr, y_tr)

    if len(y_te) == 0:
        empty = {"r2": None, "rmse": None, "mae": None, "slope": None, "intercept": None, "n": 0}
        rf_m = dict(empty)
        base_global = dict(empty)
        base_month = dict(empty)
        imp = {"method": "permutation_importance", "n_repeats": 10,
               "features": [], "importance_mean": [], "importance_std": []}
        print("全量训练模式：未切分测试集，跳过测试指标")
    else:
        pred = model.predict(X_te)
        rf_m = metrics(y_te, pred)

        # 基线1：全局均值
        gm = np.full_like(y_te, y_tr.mean())
        base_global = metrics(y_te, gm)

        # 基线2：最近可用时间片均值，不泄漏测试期信息
        last_period = max(train["period"].unique())
        last_month_mean = train.loc[train["period"] == last_period, TARGET].mean()
        mm = np.full_like(y_te, last_month_mean)
        base_month = metrics(y_te, mm)

        imp = model.perm_imp(X_te, pd.Series(y_te))

    corr = corr_report(pd.concat([train, test], ignore_index=True), feats)

    # run_id
    fp = hashlib.sha1(pd.util.hash_pandas_object(df.sort_index()).values.tobytes()).hexdigest()[:10]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"rf_{args.split_mode}_{ts}_{fp}"
    out = os.path.join(RUNS_DIR, run_id)
    os.makedirs(out, exist_ok=True)

    metrics_doc = {
        "run_id": run_id,
        "created_at": ts,
        "data_fingerprint": fp,
        "data_source": data_source,
        "dataset_id": args.dataset_id,
        "split": {
            "type": args.split_mode,
            "train_months": args.train_months,
            "test_months": args.test_months,
            "test_periods": args.test_periods,
            "all_periods": periods,
            "train_periods": sorted(train["period"].unique().tolist()) if len(train) else [],
            "test_periods_used": sorted(test["period"].unique().tolist()) if len(test) else [],
        },
        "features_used": feats,
        "features_missing": [c for c in ["sst_c", "chl_mg_m3", "npp_mg_c_m3_day", "ssh_m", "ssha_m"]
                             if c not in df.columns],
        "forbidden_features_blocked": ["hours", "geartype", "flag", "mmsi_present"],
        "target_transform": "log1p(fishing_hours), expm1 还原",
        "params": {
            "n_estimators": args.n_estimators,
            "max_features": args.max_features,
            "min_samples_leaf": args.min_samples_leaf,
            "max_depth": args.max_depth,
            "random_state": args.random_state,
        },
        "n_samples": {"train": len(train), "test": len(test), "total": len(df)},
        "correlation": corr,
        "baselines": {"global_mean": base_global, "month_mean": base_month},
        "random_forest": rf_m,
        "limitations": [
            "效果和基线比较以本次计算的 random_forest 与 baselines 字段为准。",
            "特征相关性以本次 correlation 字段为准；相关性可能影响重要性分配。",
            "本版本为回顾性 nowcast，不是未来预报。",
            "no_record 已剔除但未建模，需完整网格月历才能纳入。",
            "未进行空间块留出验证；时间切分方式见本次 split 字段。",
            "全量训练模式没有独立测试集，不产生泛化性能结论。",
        ],
    }

    with open(os.path.join(out, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics_doc, f, indent=2, ensure_ascii=False)
    with open(os.path.join(out, "feature_importance.json"), "w", encoding="utf-8") as f:
        json.dump(imp, f, indent=2, ensure_ascii=False)
    model.save(os.path.join(out, "model.joblib"))

    print(f"\nrun_id = {run_id}")
    print("=" * 50)
    print("指标对比（R² / RMSE / MAE）")
    print(f"  RF 模型:    R²={rf_m['r2']}  RMSE={rf_m['rmse']}  MAE={rf_m['mae']}")
    print(f"  全局均值基线: R²={base_global['r2']}  RMSE={base_global['rmse']}  MAE={base_global['mae']}")
    print(f"  月份均值基线: R²={base_month['r2']}  RMSE={base_month['rmse']}  MAE={base_month['mae']}")

    if imp.get("features"):
        print("\n特征重要性排序（permutation importance）")
        for name, v in zip(imp["features"], imp["importance_mean"]):
            bar = "█" * int(v * 100) if v > 0 else ""
            print(f"  {name:20s} {v:.4f} {bar}")

    high_corr = corr.get("high_corr_pairs", [])
    if high_corr:
        print("\n高相关因子对（|r| >= 0.8）")
        for p in high_corr:
            print(f"  {p['a']} <-> {p['b']}  r={p['pearson']}")

    print(f"\n输出: {out}")


if __name__ == "__main__":
    main()
