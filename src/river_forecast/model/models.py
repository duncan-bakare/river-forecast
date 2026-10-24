"""Forecast models for Clonmel. All predict the change in level dy = y(t+h) - y(t)."""

import numpy as np
import pandas as pd

# Upstream-lag linear baseline: own recent change plus upstream changes over windows that span
# the measured travel times (D18). No rain, so it shows what the river network alone gives.
LINEAR_UPSTREAM = ("16139", "16137", "16009", "16008", "16007", "16012", "16003", "16013")


OWN_COLUMNS = ("dy_1", "dy_3", "dy_6", "dy_24")
UPSTREAM_WINDOWS = (6, 12, 24)


def station_block(station):
    return [f"du_{station}_{w}" for w in UPSTREAM_WINDOWS]


def linear_columns():
    cols = list(OWN_COLUMNS)
    for s in LINEAR_UPSTREAM:
        cols += station_block(s)
    return cols


class Persistence:
    name = "persistence"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return pd.Series(0.0, index=X.index)


class UpstreamLinear:
    """OLS on Clonmel's own recent changes plus upstream level changes, with proper handling of
    missing stations (no silent fallback to persistence).

    Each upstream station is a block of columns; a row can use a station only if the whole block
    is present. One OLS model is fitted per station subset that is common in training (patterns seen
    at least `min_rows` times, all subsets missing at most two stations, and own-columns-only), each
    on every training row where that subset is available. At prediction, each row uses the largest
    fitted subset contained in the stations it has. If the *own* columns are missing the prediction
    is NaN, never 0, so a missing input can't masquerade as 'no change'."""

    name = "upstream_linear"

    def __init__(self, stations=LINEAR_UPSTREAM, ridge=1e-3, min_rows=500, max_dropped=2):
        self.stations = tuple(stations)
        self.ridge = ridge
        self.min_rows = min_rows
        self.max_dropped = max_dropped

    def _masks(self, X):
        """int bitmask per row of which stations have a complete block; plus own-columns-ok flag."""
        bits = np.zeros(len(X), dtype=np.int64)
        for i, s in enumerate(self.stations):
            bits |= (X[station_block(s)].notna().all(axis=1).to_numpy().astype(np.int64)) << i
        return bits, X[list(OWN_COLUMNS)].notna().all(axis=1).to_numpy()

    def _cols(self, mask):
        cols = list(OWN_COLUMNS)
        for i, s in enumerate(self.stations):
            if mask >> i & 1:
                cols += station_block(s)
        return cols

    def _ols(self, A, y):
        A = np.column_stack([np.ones(len(A)), A])
        reg = self.ridge * np.eye(A.shape[1])
        reg[0, 0] = 0
        return np.linalg.solve(A.T @ A + reg, A.T @ y)

    def fit(self, X, y):
        bits, own_ok = self._masks(X)
        ok = own_ok & y.notna().to_numpy()
        full = (1 << len(self.stations)) - 1
        cands = {0}
        for m, n in zip(*np.unique(bits[ok], return_counts=True), strict=True):
            if n >= self.min_rows:
                cands.add(int(m))
        for i in range(len(self.stations)):
            cands.add(full & ~(1 << i))
            for j in range(i + 1, len(self.stations)):
                cands.add(full & ~(1 << i) & ~(1 << j))
        cands.add(full)
        yv = y.to_numpy()
        self.models_ = {}
        for m in sorted(cands):
            rows = ok & ((bits & m) == m)
            if rows.sum() < self.min_rows:
                continue
            cols = self._cols(m)
            self.models_[m] = (cols, self._ols(X[cols].to_numpy()[rows], yv[rows]), int(rows.sum()))
        self.full_mask_ = full
        return self

    def _choose(self, avail):
        best = None
        for m, (_, _, n) in self.models_.items():
            if m & ~avail == 0:
                key = (bin(m).count("1"), n)
                if best is None or key > best[0]:
                    best = (key, m)
        return best[1]

    def predict(self, X):
        bits, own_ok = self._masks(X)
        pred = np.full(len(X), np.nan)
        used = np.full(len(X), -1, dtype=np.int64)
        for avail in np.unique(bits[own_ok]):
            m = self._choose(int(avail))
            cols, coef, _ = self.models_[m]
            rows = own_ok & (bits == avail)
            pred[rows] = coef[0] + X[cols].to_numpy()[rows] @ coef[1:]
            used[rows] = bin(m).count("1")
        self.used_ = pd.Series(used, index=X.index)  # stations used per row (-1 = no forecast)
        return pd.Series(pred, index=X.index)

    def usage(self, mask=None):
        """Share of forecastable rows by number of upstream stations used, and the NaN rate."""
        u = self.used_ if mask is None else self.used_[mask]
        ok = u[u >= 0]
        return {
            "no_forecast_rate": float((u < 0).mean()),
            "full_model_rate": float((ok == len(self.stations)).mean()),
            "own_only_rate": float((ok == 0).mean()),
            "mean_stations": float(ok.mean()),
        }


def cqr_scores(lo, hi, y):
    """Conformalised-quantile-regression nonconformity scores (positive = outside the raw interval)."""
    return np.maximum(lo - y, y - hi)


def conformal_quantile(scores, alpha=0.1):
    """Finite-sample (1 - alpha) quantile of the scores (0 if there are none)."""
    s = np.sort(np.asarray(scores, float))
    s = s[~np.isnan(s)]
    if not len(s):
        return 0.0
    k = min(len(s) - 1, int(np.ceil((len(s) + 1) * (1 - alpha))) - 1)
    return float(s[k])


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

    def raw_interval(self, X):
        """Uncalibrated 5%/95% quantile predictions of dy."""
        return pd.Series(self.lo.predict(X), index=X.index), pd.Series(self.hi.predict(X), index=X.index)

    def predict_interval(self, X, q=None):
        """CQR interval for dy. `q` overrides the stored corrections ({"normal": m, "high": m})."""
        q = q or self.q_
        corr = np.where(X["y"] >= self.high_level, q["high"], q["normal"])
        lo, hi = self.raw_interval(X)
        return lo - corr, hi + corr
