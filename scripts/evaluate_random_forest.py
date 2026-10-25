import argparse
import json
import os
import sys

# 保证从任意目录运行都能 import 到项目根下的 models 包
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
from joblib import load

from models.features import TARGET, build_feature_list
from scripts.train_random_forest import load_csvs, load_from_db, metrics

RUNS_DIR = os.environ.get("FISHERIES_MODEL_RUNS_DIR", os.path.join(ROOT, "model-artifacts", "runs"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", required=True)
    ap.add_argument("--source", choices=["csv", "sqlite"], default="sqlite",
                    help="复算用的数据源，要和训练时一致")
    ap.add_argument("--csv_dir", default=os.path.join(ROOT, "fleet-monthly-csvs-10-v3-2012"))
    ap.add_argument("--db_path", default=os.environ.get("FISHERIES_RESEARCH_DB", os.path.join(ROOT, "data", "database", "fisheries_forecast.sqlite3")))
    ap.add_argument("--dataset_id", default="gfw_v3.0")
    args = ap.parse_args()

    run_dir = os.path.join(RUNS_DIR, args.run_id)
    with open(os.path.join(run_dir, "metrics.json"), encoding="utf-8") as f:
        doc = json.load(f)
    with open(os.path.join(run_dir, "feature_importance.json"), encoding="utf-8") as f:
        imp = json.load(f)

    if args.source == "sqlite":
        df = load_from_db(args.db_path, args.dataset_id)
    else:
        df = load_csvs(args.csv_dir)

    split_type = doc["split"].get("type", "manual")
    if split_type == "manual":
        tr_m = doc["split"]["train_months"]
        te_m = doc["split"]["test_months"]
        train = df[df["month"].isin(tr_m)]
        test = df[df["month"].isin(te_m)]
    else:
        df["period"] = df["year"].astype(int) * 12 + df["month"].astype(int)
        train = df[df["period"].isin(doc["split"].get("train_periods", []))]
        test = df[df["period"].isin(doc["split"].get("test_periods_used", []))]

    feats = build_feature_list(test)
    rf = load(os.path.join(run_dir, "model.joblib"))
    pred = rf.predict(test[feats])
    recomputed = metrics(test[TARGET].values, pred)

    print("=" * 50)
    print(f"run_id        : {doc['run_id']}")
    print(f"split         : {split_type}")
    print(f"features      : {feats}")
    print("-" * 50)
    print(f"RF   R²={recomputed['r2']:.4f}  RMSE={recomputed['rmse']:.2f}  MAE={recomputed['mae']:.2f}")
    print(f"     slope={recomputed['slope']:.3f}  intercept={recomputed['intercept']:.3f}")
    print(f"base month R²={doc['baselines']['month_mean']['r2']:.4f}")
    print(f"base glob  R²={doc['baselines']['global_mean']['r2']:.4f}")
    print("-" * 50)
    print("特征重要性 (permutation):")
    for name, v in zip(imp["features"], imp["importance_mean"]):
        print(f"  {name:20s} {v:.4f}")
    print("=" * 50)


if __name__ == "__main__":
    main()
