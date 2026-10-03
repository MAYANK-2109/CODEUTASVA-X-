"""Reads the gradient-boosted trees that app.ml.train exports from LightGBM,
and scores one row at a time. The server needs no LightGBM to run them."""

import math


def export(booster) -> dict:
    """A LightGBM booster as flat arrays: per tree, the feature and threshold
    of each split, its two children, and the value of each leaf."""
    dump = booster.dump_model()
    trees = []
    for info in dump["tree_info"]:
        feature, threshold, left, right, value = [], [], [], [], []

        def add(node: dict) -> int:
            index = len(feature)
            for column in (feature, threshold, left, right, value):
                column.append(0)
            if "leaf_value" in node:
                feature[index], value[index] = -1, node["leaf_value"]
                return index
            if node["decision_type"] != "<=":
                raise ValueError("only numeric splits are supported")
            feature[index], threshold[index] = node["split_feature"], node["threshold"]
            left[index] = add(node["left_child"])
            right[index] = add(node["right_child"])
            return index

        add(info["tree_structure"])
        trees.append({"feature": feature, "threshold": threshold, "left": left, "right": right, "value": value})
    return {"features": dump["feature_names"], "objective": dump["objective"].split()[0], "trees": trees}


def raw_score(model: dict, row: dict[str, float]) -> float:
    """Sum of the leaf each tree sends the row to."""
    x = [row[name] for name in model["features"]]
    total = 0.0
    for tree in model["trees"]:
        feature, node = tree["feature"], 0
        while feature[node] >= 0:
            node = tree["left"][node] if x[feature[node]] <= tree["threshold"][node] else tree["right"][node]
        total += tree["value"][node]
    return total


def predict(model: dict, row: dict[str, float]) -> float:
    """A probability for a binary model, the predicted value otherwise."""
    score = raw_score(model, row)
    return 1 / (1 + math.exp(-score)) if model["objective"] == "binary" else score
