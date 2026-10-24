import numpy as np
import pytest

from river_forecast.live.trees import TreeModel

lgb = pytest.importorskip("lightgbm")


def _data(seed=0, n=1500, p=6, nan_rate=0.2):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, p))
    X[:, 2] = np.round(X[:, 2])  # many exact zeros (matters for zero-as-missing)
    y = X[:, 0] * 2 + np.where(X[:, 1] > 0, 1.0, -1.0) + 0.5 * X[:, 2] + rng.normal(0, 0.2, n)
    Xn = X.copy()
    Xn[rng.random(X.shape) < nan_rate] = np.nan
    return Xn, y


def _model(params, X, y):
    return lgb.train(
        {"verbose": -1, "num_leaves": 15, "min_data_in_leaf": 10, "seed": 1, **params},
        lgb.Dataset(X, y),
        num_boost_round=40,
    )


@pytest.mark.parametrize(
    "params",
    [
        {"objective": "regression"},
        {"objective": "quantile", "alpha": 0.05},
        {"objective": "quantile", "alpha": 0.95},
        {"objective": "regression", "use_missing": False},  # missing type: none (NaN treated as 0)
        {"objective": "regression", "zero_as_missing": True},  # missing type: zero
        {"objective": "regression", "feature_fraction": 0.7, "bagging_fraction": 0.8, "bagging_freq": 1},
    ],
)
def test_matches_lightgbm_including_nans(params):
    X, y = _data()
    booster = _model(params, X[:1000], y[:1000])
    tm = TreeModel.from_text(booster.model_to_string())
    test = X[1000:]
    assert np.allclose(tm.predict(test), booster.predict(test), atol=1e-10, rtol=0)
    clean = np.nan_to_num(test, nan=0.37)
    assert np.allclose(tm.predict(clean), booster.predict(clean), atol=1e-10, rtol=0)
    assert len(tm.trees) == 40


def test_all_nan_row_and_single_row():
    X, y = _data()
    booster = _model({"objective": "regression"}, X, y)
    tm = TreeModel.from_text(booster.model_to_string())
    row = np.full((1, X.shape[1]), np.nan)
    assert tm.predict(row)[0] == pytest.approx(booster.predict(row)[0], abs=1e-10)
    assert tm.predict(X[0])[0] == pytest.approx(booster.predict(X[:1])[0], abs=1e-10)  # 1-D input


def test_single_leaf_trees():
    X, y = _data(n=60)
    booster = _model({"objective": "regression", "min_data_in_leaf": 1000}, X, y)  # cannot split
    tm = TreeModel.from_text(booster.model_to_string())
    assert all(t.single for t in tm.trees)
    assert np.allclose(tm.predict(X), booster.predict(X), atol=1e-10)


def test_unsupported_models_fail_loudly():
    X, y = _data()
    binary = _model({"objective": "binary"}, X, (y > 0).astype(float))
    with pytest.raises(ValueError, match="objective"):
        TreeModel.from_text(binary.model_to_string())
    Xc = np.where(np.isnan(X), 0, np.abs(np.round(X))).astype(float)
    cat = lgb.train(
        {"objective": "regression", "verbose": -1, "min_data_in_leaf": 5, "min_data_per_group": 5, "cat_smooth": 1},
        lgb.Dataset(Xc, y, categorical_feature=[2]),
        num_boost_round=5,
    )
    if "num_cat=0" not in cat.model_to_string():
        with pytest.raises(ValueError, match="categorical"):
            TreeModel.from_text(cat.model_to_string())
