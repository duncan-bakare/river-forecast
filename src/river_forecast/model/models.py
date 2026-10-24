"""Forecast models for Clonmel. All predict the change in level dy = y(t+h) - y(t)."""

import numpy as np
import pandas as pd

# Upstream-lag linear baseline: own recent change plus upstream changes over windows that span
# the measured travel times (D18). No rain, so it shows what the river network alone gives.
LINEAR_UPSTREAM = ("16139", "16137", "16009", "16008", "16007", "16012", "16003", "16013")


def linear_columns():
    cols = ["dy_1", "dy_3", "dy_6", "dy_24"]
    for s in LINEAR_UPSTREAM:
        cols += [f"du_{s}_6", f"du_{s}_12", f"du_{s}_24"]
    return cols


class Persistence:
    name = "persistence"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return pd.Series(0.0, index=X.index)


class UpstreamLinear:
    """OLS on upstream level changes. Where an input is missing it falls back to
    persistence (dy = 0) and counts the fallback."""

    name = "upstream_linear"

    def __init__(self, ridge=1e-3):
        self.cols = linear_columns()
        self.ridge = ridge

    def fit(self, X, y):
        d = X[self.cols].assign(_y=y).dropna()
        A = np.column_stack([np.ones(len(d)), d[self.cols].to_numpy()])
        reg = self.ridge * np.eye(A.shape[1])
        reg[0, 0] = 0
        self.coef_ = np.linalg.solve(A.T @ A + reg, A.T @ d._y.to_numpy())
        self.n_train_ = len(d)
        return self

    def predict(self, X):
        A = X[self.cols]
        ok = A.notna().all(axis=1)
        pred = pd.Series(0.0, index=X.index)
        pred[ok] = self.coef_[0] + A[ok].to_numpy() @ self.coef_[1:]
        self.fallback_rate_ = float(1 - ok.mean())
        return pred


class LGBMQuantile:
    """LightGBM point model (L2) plus 5%/95% quantile models, conformalised (CQR) on a
    calibration set, separately for normal and high-flow regimes (Mondrian CQR)."""

    name = "lightgbm"

    def __init__(self, high_level, alpha=0.1, params=None, seed=0):
        self.high_level = high_level
        self.alpha = alpha
        self.params = {
            "n_estimators": 2000,
            "learning_rate": 0.03,
            "num_leaves": 63,
            "min_child_samples": 50,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.8,
            "random_state": seed,
            "verbose": -1,
            **(params or {}),
        }

    def _model(self, **kw):
        import lightgbm as lgb

        return lgb.LGBMRegressor(**{**self.params, **kw})

    def fit(self, X, y, X_val, y_val):
        import lightgbm as lgb

        stop = [lgb.early_stopping(100, verbose=False)]
        tr, va = y.notna(), y_val.notna()
        self.point = self._model().fit(X[tr], y[tr], eval_set=[(X_val[va], y_val[va])], callbacks=stop)
        n = self.point.best_iteration_ or self.params["n_estimators"]
        self.lo = self._model(objective="quantile", alpha=self.alpha / 2, n_estimators=n).fit(X[tr], y[tr])
        self.hi = self._model(objective="quantile", alpha=1 - self.alpha / 2, n_estimators=n).fit(X[tr], y[tr])
        # CQR calibration
        lo, hi = self.lo.predict(X_val[va]), self.hi.predict(X_val[va])
        score = np.maximum(lo - y_val[va].to_numpy(), y_val[va].to_numpy() - hi)
        regime = (X_val.loc[va, "y"] >= self.high_level).to_numpy()
        self.q_ = {}
        for name, m in (("normal", ~regime), ("high", regime)):
            s = np.sort(score[m])
            k = min(len(s) - 1, int(np.ceil((len(s) + 1) * (1 - self.alpha))) - 1)
            self.q_[name] = float(s[k]) if len(s) else 0.0
        self.n_cal_ = {"normal": int((~regime).sum()), "high": int(regime.sum())}
        return self

    def predict(self, X):
        return pd.Series(self.point.predict(X), index=X.index)

    def predict_interval(self, X):
        q = np.where(X["y"] >= self.high_level, self.q_["high"], self.q_["normal"])
        lo = self.lo.predict(X) - q
        hi = self.hi.predict(X) + q
        return pd.Series(lo, index=X.index), pd.Series(hi, index=X.index)
