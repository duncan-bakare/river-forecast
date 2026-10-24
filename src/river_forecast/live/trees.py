"""Minimal LightGBM text-model evaluator (numerical splits, regression/quantile objectives).

The live Lambda has numpy and pandas but not scipy, and lightgbm hard-imports scipy; adding it would break
Lambda's unpacked-size limit. The text format is simple, so we evaluate the trees directly. Parity with
`lightgbm.Booster.predict` is tested (including NaNs and all three missing-value modes) and re-checked on the
real models by scripts/export_live_assets.py. Anything outside what we support fails loudly at load time.
"""

import numpy as np

ZERO_THRESHOLD = 1e-35  # LightGBM kZeroThreshold


class Tree:
    def __init__(self, fields):
        if int(fields.get("num_cat", "0")) != 0:
            raise ValueError("categorical splits are not supported")
        if int(fields.get("is_linear", "0")) != 0:
            raise ValueError("linear trees are not supported")
        self.leaf_value = np.array(fields["leaf_value"].split(), dtype=float)
        n_leaves = int(fields["num_leaves"])
        self.single = n_leaves == 1
        if self.single:
            return
        self.feature = np.array(fields["split_feature"].split(), dtype=int)
        self.threshold = np.array(fields["threshold"].split(), dtype=float)
        dt = np.array(fields["decision_type"].split(), dtype=int)
        if (dt & 1).any():
            raise ValueError("categorical decision types are not supported")
        self.default_left = (dt & 2) > 0
        self.missing_type = (dt >> 2) & 3  # 0 none, 1 zero, 2 NaN
        self.left = np.array(fields["left_child"].split(), dtype=int)
        self.right = np.array(fields["right_child"].split(), dtype=int)

    def predict(self, a):
        n = len(a)
        if self.single:
            return np.full(n, self.leaf_value[0])
        node = np.zeros(n, dtype=int)  # >= 0 internal node, < 0 is ~leaf
        out = np.zeros(n)
        active = np.ones(n, dtype=bool)
        while active.any():
            idx = np.flatnonzero(active)
            nd = node[idx]
            x = a[idx, self.feature[nd]]
            mt = self.missing_type[nd]
            nan = np.isnan(x)
            x = np.where(nan & (mt != 2), 0.0, x)  # NaN is 0 unless the node treats NaN as missing
            missing = ((mt == 2) & nan) | ((mt == 1) & (np.abs(x) <= ZERO_THRESHOLD))
            go_left = np.where(missing, self.default_left[nd], x <= self.threshold[nd])
            nxt = np.where(go_left, self.left[nd], self.right[nd])
            leaf = nxt < 0
            out[idx[leaf]] = self.leaf_value[~nxt[leaf]]
            active[idx[leaf]] = False
            node[idx[~leaf]] = nxt[~leaf]
        return out


class TreeModel:
    def __init__(self, trees, feature_names, objective):
        self.trees, self.feature_names, self.objective = trees, feature_names, objective

    @classmethod
    def from_text(cls, text):
        header, blocks, cur = {}, [], None
        for line in text.splitlines():
            if line.startswith("end of trees"):
                break
            if line.startswith("Tree="):
                cur = {}
                blocks.append(cur)
            elif "=" in line and not line.startswith("["):
                k, v = line.split("=", 1)
                (header if cur is None else cur)[k] = v
        if header.get("num_class", "1") != "1" or int(header.get("num_tree_per_iteration", "1")) != 1:
            raise ValueError("only single-output models are supported")
        objective = header.get("objective", "").split()[0] if header.get("objective") else ""
        if objective not in ("regression", "quantile"):
            raise ValueError(f"unsupported objective {objective!r}")
        n_declared = len(header.get("tree_sizes", "").split())
        trees = [Tree(b) for b in blocks]
        if n_declared and n_declared != len(trees):
            raise ValueError(f"expected {n_declared} trees, parsed {len(trees)}")
        return cls(trees, header.get("feature_names", "").split(), objective)

    @classmethod
    def from_file(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls.from_text(fh.read())

    def predict(self, a):
        a = np.asarray(a, dtype=float)
        if a.ndim == 1:
            a = a[None, :]
        total = np.zeros(len(a))
        for t in self.trees:
            total += t.predict(a)
        return total
