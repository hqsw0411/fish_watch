import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.inspection import permutation_importance
from joblib import dump, load

from .features import log1p_target, expm1_target


class RFFishing:
    def __init__(self, random_state=42, **kw):
        self.rs = random_state
        self.m = RandomForestRegressor(
            random_state=random_state,
            n_jobs=-1,
            **kw,
        )
        self.feat_names = None

    def fit(self, X: pd.DataFrame, y):
        self.feat_names = X.columns.tolist()
        self.m.fit(X, log1p_target(np.asarray(y, dtype=float)))

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return expm1_target(self.m.predict(X))

    def perm_imp(self, X_test, y_test, n_repeats=10):
        # n_jobs=1：并行 perm 会把数据复制多份，样本量大时内存翻倍，串行更稳
        r = permutation_importance(
            self.m, X_test, log1p_target(np.asarray(y_test, dtype=float)),
            n_repeats=n_repeats, random_state=self.rs, n_jobs=1,
            scoring="r2",
        )
        order = np.argsort(r.importances_mean)[::-1]
        feats = [self.feat_names[i] for i in order]
        return {
            "method": "permutation_importance",
            "scoring": "r2",
            "n_repeats": n_repeats,
            "features": feats,
            "importance_mean": [round(float(r.importances_mean[i]), 6) for i in order],
            "importance_std": [round(float(r.importances_std[i]), 6) for i in order],
        }

    def save(self, path):
        dump(self.m, path)

    def load(self, path):
        self.m = load(path)
