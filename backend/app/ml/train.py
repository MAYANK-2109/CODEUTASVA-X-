"""Train and evaluate the models behind the alerts, from three data sources.

  uv run python -m app.ml.train

1. Sudden-move risk model: logistic regression on 13 years of real NSE
   prices, predicting whether a holding will have a sudden move in the next
   five sessions. It also records how often past sudden moves continued.
2. Sentiment: FinBERT and VADER scored against the Financial PhraseBank
   (Kaggle: ankurzing/sentiment-analysis-for-financial-news), with label
   cut-offs calibrated on half of it.
3. Concentration benchmark: the share of the largest position across
   institutional portfolios (Hugging Face: Kasher13/Institutional-Holdings-Dashboard).
4. Power assets: Indian power stations of listed owners, with coordinates and
   capacity (WRI Global Power Plant Database).
5. Storm impact: how companies with assets near a cyclone's landfall moved,
   against companies with none (NOAA IBTrACS tracks, North Indian Ocean).
6. Hedge cost: where India VIX and the CBOE VIX stand in their own history,
   and how implied volatility has compared with what followed (FRED, Yahoo).
7. Impact model: LightGBM trees on prices, macro indicators, sector and
   weather alert days, for the chance of a sharp fall and the size of the
   downside over the next five sessions. Compared against price-only models.
   Needs the training extras:  uv sync --group train

Results are written to data/trained/ as JSON and read by app.ml.alerts.
"""

import csv
import io
import json
import sys
import zipfile
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from app.ml import assets, gbm
from app.ml.impact import company_alert_dates, sector_codes
from app.ml.features import (
    DOWNSIDE_LEVEL, FALL_SIZE, FEATURES, HORIZON, IMPACT_FEATURES, MACRO_FEATURES, PRICE_FEATURES, SHOCK_Z,
    abnormal_returns, impact_features, macro_features, risk_features,
)
from app.tools.market import NIFTY, SECTORS, get_cross_assets, get_history

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
TRAINED_DIR = DATA_DIR / "trained"
DATASET_CACHE = DATA_DIR / "cache" / "datasets"
TEST_FROM = "2023-01-01"

PHRASEBANK_URL = "https://www.kaggle.com/api/v1/datasets/download/ankurzing/sentiment-analysis-for-financial-news"
HOLDINGS_API = "https://huggingface.co/datasets/Kasher13/Institutional-Holdings-Dashboard/resolve/main/api"
POWER_PLANTS_URL = "https://wri-dataportal-prod.s3.amazonaws.com/manual/global_power_plant_database_v_1_3.zip"
IBTRACS_URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
               "v04r01/access/csv/ibtracs.NI.list.v04r01.csv")
FRED_VIX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
STORM_RADIUS_KM = 300
TROPICAL_STORM_KT = 34


# --------------------------------------------------------------------------- sudden-move model


def _fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 60) -> np.ndarray:
    """Logistic regression by Newton's method; the intercept is not penalised."""
    x1 = np.column_stack([np.ones(len(x)), x])
    w = np.zeros(x1.shape[1])
    penalty = np.eye(x1.shape[1]) * l2
    penalty[0, 0] = 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-x1 @ w))
        gradient = x1.T @ (p - y) + penalty @ w
        hessian = (x1 * (p * (1 - p))[:, None]).T @ x1 + penalty
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def _auc(y: np.ndarray, score: np.ndarray) -> float:
    ranks = pd.Series(score).rank().to_numpy()
    positives = y == 1
    n_pos, n_neg = positives.sum(), (~positives).sum()
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def train_shock_model() -> dict:
    tickers = [f"{symbol}.NS" for symbol in SECTORS]
    closes, source = get_history(tickers)
    if closes is None:
        raise RuntimeError("Price history unavailable; cannot train")

    rows = []
    for ticker in tickers:
        if ticker not in closes.columns or closes[ticker].notna().sum() < 600:
            continue
        days = risk_features(abnormal_returns(closes[ticker], closes[NIFTY]))
        days["ticker"] = ticker
        rows.append(days.dropna(subset=[*FEATURES, "shock_ahead"]))
    data = pd.concat(rows).sort_index()

    train, test = data[data.index < TEST_FROM], data[data.index >= TEST_FROM]
    mean, std = train[FEATURES].mean(), train[FEATURES].std().replace(0, 1)
    weights = _fit_logistic(((train[FEATURES] - mean) / std).to_numpy(), train["shock_ahead"].to_numpy())

    def predict(frame: pd.DataFrame) -> np.ndarray:
        z = ((frame[FEATURES] - mean) / std).to_numpy()
        return 1 / (1 + np.exp(-(weights[0] + z @ weights[1:])))

    p_train, p_test = predict(train), predict(test)
    y_test = test["shock_ahead"].to_numpy()
    # "Elevated" means the model's top tenth of days, a cut-off fixed on the training years.
    elevated = float(np.quantile(p_train, 0.90))
    high = p_test >= elevated
    low = p_test <= float(np.quantile(p_train, 0.50))

    # Direction after a sudden move, for honest wording in the alerts.
    shocks = test[test["z"].abs() >= SHOCK_Z].dropna(subset=["next"])
    same_way = (shocks["next"] * np.sign(shocks["z"])) > 0
    drops, jumps = shocks["z"] < 0, shocks["z"] > 0

    return {
        "trained_on": date.today().isoformat(),
        "data": f"Daily closes of {data['ticker'].nunique()} NSE stocks from Yahoo Finance ({source})",
        "definition": f"Sudden move: a daily return, beyond what the stock's beta to the Nifty explains, "
                      f"of at least {SHOCK_Z} standard deviations of its last 60 sessions. The model "
                      f"estimates the chance of one in the next {HORIZON} sessions.",
        "features": FEATURES,
        "mean": {k: round(float(v), 6) for k, v in mean.items()},
        "std": {k: round(float(v), 6) for k, v in std.items()},
        "intercept": round(float(weights[0]), 6),
        "weights": {k: round(float(v), 6) for k, v in zip(FEATURES, weights[1:])},
        "elevated_cutoff": round(elevated, 4),
        "train": {"days": int(len(train)), "period": f"{train.index[0].date()} to {train.index[-1].date()}",
                  "base_rate": round(float(train["shock_ahead"].mean()), 3)},
        "test": {
            "days": int(len(test)), "period": f"{test.index[0].date()} to {test.index[-1].date()}",
            "base_rate": round(float(y_test.mean()), 3),
            "auc": round(_auc(y_test, p_test), 3),
            "rate_when_elevated": round(float(y_test[high].mean()), 3),
            "elevated_days": int(high.sum()),
            "rate_when_calm": round(float(y_test[low].mean()), 3),
            "calm_days": int(low.sum()),
        },
        "after_a_sudden_move": {
            "note": "Direction is not predictable: a model trained to predict continuation scored AUC 0.50.",
            "drops": {"cases": int(drops.sum()), "kept_falling": round(float(same_way[drops].mean()), 3)},
            "jumps": {"cases": int(jumps.sum()), "kept_rising": round(float(same_way[jumps].mean()), 3)},
        },
    }


# --------------------------------------------------------------------------- impact model

VALID_FROM = "2021-01-01"   # 2021-22 picks the number of trees; 2023 onwards is only ever tested on
MAX_TREES = 300
GBM_PARAMS = {"learning_rate": 0.05, "num_leaves": 8, "min_data_in_leaf": 400, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0, "use_missing": False,
              "verbose": -1, "seed": 7, "num_threads": 4, "deterministic": True}


def impact_dataset() -> tuple[pd.DataFrame, str]:
    tickers = [f"{symbol}.NS" for symbol in SECTORS]
    closes, source = get_history(tickers)
    macro = get_cross_assets()
    if closes is None or macro is None:
        raise RuntimeError("Price or macro history unavailable; cannot train")
    macro_days = macro_features(closes[NIFTY], macro)
    codes, alerts = sector_codes(), company_alert_dates()
    rows = []
    for symbol, sector in SECTORS.items():
        ticker = f"{symbol}.NS"
        if ticker not in closes.columns or closes[ticker].notna().sum() < 600:
            continue
        days = impact_features(closes[ticker], closes[NIFTY], macro_days, codes[sector], alerts.get(symbol, []))
        days["ticker"] = ticker
        rows.append(days.dropna(subset=[*IMPACT_FEATURES, "fall_ahead", "forward_return"]))
    return pd.concat(rows).sort_index(), source


def _fit_gbm(fit: pd.DataFrame, valid: pd.DataFrame, features: list[str], target: str, **objective):
    """Trees are added until the 2021-22 score stops improving; the model is then
    refitted on all the years before the test period with that many trees."""
    import lightgbm as lgb

    params = {**GBM_PARAMS, **objective}
    early = lgb.train(params, lgb.Dataset(fit[features], fit[target]), MAX_TREES,
                      valid_sets=[lgb.Dataset(valid[features], valid[target])],
                      callbacks=[lgb.early_stopping(30, verbose=False)])
    rounds = max(early.best_iteration, 20)
    both = pd.concat([fit, valid])
    return lgb.train(params, lgb.Dataset(both[features], both[target]), rounds), early


def _pinball(actual: np.ndarray, predicted: np.ndarray, level: float) -> float:
    gap = actual - predicted
    return float(np.mean(np.maximum(level * gap, (level - 1) * gap)))


def train_impact_model() -> dict:
    data, source = impact_dataset()
    fit, valid = data[data.index < VALID_FROM], data[(data.index >= VALID_FROM) & (data.index < TEST_FROM)]
    test = data[data.index >= TEST_FROM]
    y = test["fall_ahead"].to_numpy()

    # The same question asked of one data source and of all of them.
    feature_sets = {"60-day volatility alone": ["vol_60"], "price history": PRICE_FEATURES,
                    "price and macro": [*PRICE_FEATURES, *MACRO_FEATURES],
                    "price, macro, sector and weather": IMPACT_FEATURES}
    comparison = {}
    for label, features in feature_sets.items():
        model, _ = _fit_gbm(fit, valid, features, "fall_ahead", objective="binary")
        comparison[label] = round(_auc(y, model.predict(test[features])), 3)

    fall, fall_early = _fit_gbm(fit, valid, IMPACT_FEATURES, "fall_ahead", objective="binary")
    downside, _ = _fit_gbm(fit, valid, IMPACT_FEATURES, "forward_return", objective="quantile", alpha=DOWNSIDE_LEVEL)

    # "Elevated" is the top tenth of days, a cut-off set on 2021-22, which the early model had not seen.
    cutoff = float(np.quantile(fall_early.predict(valid[IMPACT_FEATURES]), 0.90))
    p_fall = fall.predict(test[IMPACT_FEATURES])
    high = p_fall >= cutoff

    actual = test["forward_return"].to_numpy()
    predicted = downside.predict(test[IMPACT_FEATURES])
    usual = test["usual_downside"].to_numpy()

    models = {"fall": gbm.export(fall), "downside": gbm.export(downside)}
    sample = test[IMPACT_FEATURES].iloc[:: max(1, len(test) // 300)]
    for name, booster in (("fall", fall), ("downside", downside)):
        ours = np.array([gbm.predict(models[name], row) for row in sample.to_dict("records")])
        if np.abs(ours - booster.predict(sample)).max() > 1e-9:
            raise RuntimeError(f"exported {name} model does not reproduce LightGBM")

    gain = dict(zip(IMPACT_FEATURES, fall.feature_importance("gain")))
    total_gain = sum(gain.values()) or 1.0
    both = pd.concat([fit, valid])
    return {
        "trained_on": date.today().isoformat(),
        "method": "LightGBM gradient-boosted trees, exported and run without LightGBM on the server",
        "data": f"{len(data):,} stock-days of {data['ticker'].nunique()} NSE stocks ({source}), with India VIX, "
                f"Brent and USD/INR from Yahoo Finance and weather alert days from Open-Meteo",
        "definition": f"Sharp fall: a loss of {FALL_SIZE:.0%} or more over the next {HORIZON} sessions. "
                      f"Downside: the {HORIZON}-session return that only {DOWNSIDE_LEVEL:.0%} of cases fall below.",
        "features": IMPACT_FEATURES,
        "sectors": sector_codes(),
        "horizon_sessions": HORIZON,
        "fall_size": FALL_SIZE,
        "fit_period": f"{both.index[0].date()} to {both.index[-1].date()}",
        "test_period": f"{test.index[0].date()} to {test.index[-1].date()}",
        "test_days": int(len(test)),
        "fall": {
            "trees": fall.num_trees(),
            "auc": round(_auc(y, p_fall), 3),
            "auc_by_data_used": comparison,
            "base_rate": round(float(y.mean()), 3),
            "elevated_cutoff": round(cutoff, 4),
            "share_of_days_elevated": round(float(high.mean()), 3),
            "rate_when_elevated": round(float(y[high].mean()), 3),
            "rate_otherwise": round(float(y[~high].mean()), 3),
            "what_drives_it": {k: round(v / total_gain, 3) for k, v in
                               sorted(gain.items(), key=lambda kv: -kv[1])[:6]},
        },
        "downside": {
            "trees": downside.num_trees(),
            "level": DOWNSIDE_LEVEL,
            "share_of_outcomes_worse": round(float((actual < predicted).mean()), 3),
            "share_worse_than_usual_downside": round(float((actual < usual).mean()), 3),
            "pinball_loss": round(_pinball(actual, predicted, DOWNSIDE_LEVEL), 5),
            "pinball_loss_usual_downside": round(_pinball(actual, usual, DOWNSIDE_LEVEL), 5),
        },
        "models": models,
    }


# --------------------------------------------------------------------------- sentiment


def _download(url: str, target: Path) -> Path:
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        response = httpx.get(url, timeout=120, follow_redirects=True)
        response.raise_for_status()
        target.write_bytes(response.content)
    return target


def load_phrasebank() -> list[tuple[str, str]]:
    archive = _download(PHRASEBANK_URL, DATASET_CACHE / "financial_phrasebank.zip")
    with zipfile.ZipFile(archive) as bundle:
        raw = bundle.read("all-data.csv").decode("latin-1")
    rows = csv.reader(io.StringIO(raw.replace("\r\n", "\n").replace("\r", "\n")))
    return [(label, text) for label, text in (r for r in rows if len(r) == 2)]


def _best_cutoffs(scores: np.ndarray, labels: np.ndarray) -> tuple[float, float, float]:
    """Positive and negative cut-offs (symmetric grid) that maximise accuracy."""
    best = (0.0, 0.05, -0.05)
    for cut in np.arange(0.02, 0.95, 0.02):
        predicted = np.where(scores >= cut, "positive", np.where(scores <= -cut, "negative", "neutral"))
        accuracy = float((predicted == labels).mean())
        if accuracy > best[0]:
            best = (accuracy, float(cut), float(-cut))
    return best


def evaluate_sentiment() -> dict:
    from app.tools import sentiment

    rows = load_phrasebank()
    labels = np.array([label for label, _ in rows])
    texts = [text for _, text in rows]
    rng = np.random.default_rng(7)
    order = rng.permutation(len(rows))
    calibrate, holdout = order[: len(order) // 2], order[len(order) // 2:]

    def assess(scores: np.ndarray) -> dict:
        _, positive, negative = _best_cutoffs(scores[calibrate], labels[calibrate])
        predicted = np.where(scores >= positive, "positive", np.where(scores <= negative, "negative", "neutral"))
        correct = predicted[holdout] == labels[holdout]
        per_class = {c: round(float(correct[labels[holdout] == c].mean()), 3) for c in ("positive", "neutral", "negative")}
        return {"positive_cutoff": round(positive, 2), "negative_cutoff": round(negative, 2),
                "holdout_accuracy": round(float(correct.mean()), 3), "holdout_recall_by_class": per_class}

    vader = np.array([sentiment._vader.polarity_scores(t)["compound"] for t in texts])
    result = {
        "dataset": "Financial PhraseBank, 4,846 sentences labelled by annotators (Kaggle: ankurzing)",
        "split": f"Cut-offs chosen on {len(calibrate)} sentences, accuracy measured on the other {len(holdout)}",
        "always_neutral_accuracy": round(float((labels[holdout] == "neutral").mean()), 3),
        "vader": assess(vader),
    }
    if sentiment._load_finbert():
        finbert = np.concatenate(
            [np.array(sentiment._finbert_scores(texts[i:i + 64])) for i in range(0, len(texts), 64)]
        )
        result["finbert"] = assess(finbert)
        result["finbert"]["note"] = ("FinBERT was originally trained on this same dataset, so its accuracy "
                                     "here is optimistic for unseen headlines")
    return result


# --------------------------------------------------------------------------- concentration benchmark


def concentration_benchmark() -> dict:
    """How large the biggest position is across institutional portfolios, by quarter."""
    listing = httpx.get(f"{HOLDINGS_API}/managers/popular.json", timeout=60, follow_redirects=True).json()
    managers = {m["cik"]: m["name"] for m in listing["data"]}
    shares = []
    used = 0
    for cik in managers:
        cache = DATASET_CACHE / "institutional" / f"{cik}.json"
        try:
            path = _download(f"{HOLDINGS_API}/managers/{cik}/history.json", cache)
            history = json.loads(path.read_text())["data"]["history"]
        except (httpx.HTTPError, KeyError, ValueError):
            continue
        used += 1
        for quarter in history:
            weights = [v for k, v in quarter.items() if k.endswith("_pct") and isinstance(v, (int, float))]
            if weights:
                shares.append(max(weights))
    if len(shares) < 100:
        raise RuntimeError(f"Only {len(shares)} portfolio-quarters downloaded")
    grid = np.percentile(shares, np.arange(0, 101))
    return {
        "dataset": "SEC 13F filings of leading institutional managers (Hugging Face: Kasher13)",
        "managers": used,
        "portfolio_quarters": len(shares),
        "measure": "Share of portfolio value held in the single largest position, in percent",
        "percentiles": [round(float(v), 2) for v in grid],
        "median": round(float(grid[50]), 2),
        "p90": round(float(grid[90]), 2),
    }


# --------------------------------------------------------------------------- power assets


def build_power_assets() -> dict:
    archive = _download(POWER_PLANTS_URL, DATASET_CACHE / "wri_power_plants.zip")
    with zipfile.ZipFile(archive) as bundle:
        plants = pd.read_csv(io.BytesIO(bundle.read("global_power_plant_database.csv")), low_memory=False)
    india = plants[plants["country"] == "IND"].copy()
    india["ticker"] = india["name"].map(assets.owner_of)
    south, north, west, east = assets.INDIA_BOX
    inside = india["latitude"].between(south, north) & india["longitude"].between(west, east)
    owned = india[india["ticker"].notna() & inside].sort_values("capacity_mw", ascending=False)
    rows = [
        {"ticker": r.ticker, "name": f"{r.name.title()} power station", "lat": round(r.latitude, 3),
         "lon": round(r.longitude, 3), "kind": f"{r.primary_fuel.lower()} power", "mw": round(r.capacity_mw)}
        for r in owned.rename(columns={"name": "name"}).itertuples(index=False)
    ]
    by_owner = owned.groupby("ticker")["capacity_mw"].agg(["count", "sum"])
    return {
        "dataset": "WRI Global Power Plant Database v1.3.0",
        "india_plants_in_database": int(len(india)),
        "matched_to_listed_owner": int(len(owned)),
        "matched_mw": round(float(owned["capacity_mw"].sum())),
        "note": "Owners are matched by station name; the database's owner field is empty for most large "
                "Indian stations. Capacity is as of the database's last update (2021).",
        "by_owner": {t: {"plants": int(v["count"]), "mw": round(float(v["sum"]))} for t, v in by_owner.iterrows()},
        "plants": rows,
    }


# --------------------------------------------------------------------------- storm impact


def load_storms() -> pd.DataFrame:
    """Named storms since 2014 that reached the Indian coast at tropical-storm strength."""
    path = _download(IBTRACS_URL, DATASET_CACHE / "ibtracs_ni.csv")
    columns = ["SID", "SEASON", "NAME", "ISO_TIME", "LAT", "LON", "USA_WIND", "WMO_WIND", "DIST2LAND"]
    track = pd.read_csv(path, skiprows=[1], usecols=columns, low_memory=False)
    for column in ("SEASON", "LAT", "LON", "USA_WIND", "WMO_WIND", "DIST2LAND"):
        track[column] = pd.to_numeric(track[column], errors="coerce")
    track = track[track["SEASON"] >= 2014].copy()
    track["time"] = pd.to_datetime(track["ISO_TIME"], errors="coerce")
    track["wind"] = track[["USA_WIND", "WMO_WIND"]].max(axis=1)

    storms = []
    for sid, points in track.groupby("SID"):
        points = points.sort_values("time")
        near_india = points[
            (points["DIST2LAND"] <= 50) & points["LAT"].between(8, 24.5) & points["LON"].between(68, 89)
            & ~((points["LON"] > 79.6) & (points["LAT"] < 9.9))  # Sri Lanka
        ]
        if near_india.empty:
            continue
        arrival = near_india.iloc[0]
        before = points[(points["time"] <= arrival["time"])
                        & (points["time"] >= arrival["time"] - pd.Timedelta(hours=24))]
        wind = before["wind"].max()
        if not (wind >= TROPICAL_STORM_KT):
            continue
        storms.append({"sid": sid, "name": str(arrival["NAME"]).title(), "date": arrival["time"].date().isoformat(),
                       "lat": round(float(arrival["LAT"]), 2), "lon": round(float(arrival["LON"]), 2),
                       "wind_kt": int(wind)})
    return pd.DataFrame(storms).sort_values("date").reset_index(drop=True)


def _welch_t(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    spread = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return round(float((a.mean() - b.mean()) / spread), 2) if spread else None


def train_storm_impact() -> dict:
    storms = load_storms()
    tickers = sorted({a["ticker"] for a in assets.load_assets()})
    closes, _ = get_history([f"{t}.NS" for t in tickers])
    if closes is None:
        raise RuntimeError("Price history unavailable")
    frames = {t: abnormal_returns(closes[f"{t}.NS"], closes[NIFTY])["abnormal"]
              for t in tickers if f"{t}.NS" in closes.columns and closes[f"{t}.NS"].notna().sum() > 600}

    rows, library = [], []
    for storm in storms.itertuples(index=False):
        position = closes.index.searchsorted(pd.Timestamp(storm.date))
        if position < 300 or position + HORIZON > len(closes.index):
            continue
        exposed_moves = {}
        for ticker, abnormal in frames.items():
            after = abnormal.iloc[position:position + HORIZON]
            before = abnormal.iloc[position - 3:position]
            if after.isna().any() or before.isna().any():
                continue
            found = assets.exposure(ticker, storm.lat, storm.lon, STORM_RADIUS_KM)
            rows.append({"storm": storm.name, "ticker": ticker, "exposed": found is not None,
                         "share": (found or {}).get("share") or (1.0 if found else 0.0),
                         "wind": storm.wind_kt, "after": float(after.sum()), "before": float(before.sum())})
            if found:
                exposed_moves[ticker] = round(float(after.sum()), 4)
        library.append({"name": storm.name, "date": storm.date, "lat": storm.lat, "lon": storm.lon,
                        "wind_kt": storm.wind_kt, "exposed_moves": exposed_moves})

    data = pd.DataFrame(rows)
    exposed, others = data[data["exposed"]], data[~data["exposed"]]
    severe = exposed[exposed["wind"] >= 64]

    def describe(frame: pd.DataFrame, column: str) -> dict:
        values = frame[column].to_numpy()
        if len(values) == 0:
            return {"cases": 0}
        return {"cases": int(len(values)), "mean": round(float(values.mean()), 4),
                "median": round(float(np.median(values)), 4),
                "worst_tenth": round(float(np.quantile(values, 0.10)), 4),
                "share_negative": round(float((values < 0).mean()), 3)}

    return {
        "dataset": "NOAA IBTrACS v04r01, North Indian Ocean; prices from Yahoo Finance",
        "definition": f"A storm counts once it is within 50 km of the Indian coast at {TROPICAL_STORM_KT} kt or "
                      f"more. A company is exposed if it has a mapped asset within {STORM_RADIUS_KM} km. Moves are "
                      f"returns beyond the company's beta to the Nifty over {HORIZON} sessions from that day.",
        "storms": int(len(library)),
        "period": f"{library[0]['date']} to {library[-1]['date']}" if library else None,
        "exposed": describe(exposed, "after"),
        "not_exposed": describe(others, "after"),
        "exposed_minus_not_exposed_t": _welch_t(exposed["after"].to_numpy(), others["after"].to_numpy()),
        "exposed_severe_storms_64kt": describe(severe, "after"),
        "exposed_three_sessions_before": describe(exposed, "before"),
        "library": library,
    }


# --------------------------------------------------------------------------- hedge cost


def hedge_cost_stats() -> dict:
    import yfinance as yf

    def closes_of(ticker: str) -> pd.Series:
        frame = yf.download(ticker, period="10y", progress=False, auto_adjust=True, timeout=20)["Close"]
        return (frame.iloc[:, 0] if hasattr(frame, "columns") else frame).dropna()

    def standing(series: pd.Series) -> dict:
        window = series.iloc[-1260:]
        return {"latest": round(float(series.iloc[-1]), 2), "as_of": series.index[-1].date().isoformat(),
                "median_5y": round(float(window.median()), 2),
                "percentile_5y": round(float((window <= series.iloc[-1]).mean() * 100))}

    india_vix, nifty = closes_of("^INDIAVIX"), closes_of(NIFTY)
    # What insurance cost against what followed: implied volatility over the
    # volatility the Nifty then realised in the next 21 sessions.
    realised = nifty.pct_change().rolling(21).std().shift(-21) * np.sqrt(252) * 100
    ratio = (india_vix / realised).dropna()

    try:
        response = httpx.get(FRED_VIX_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        fred = pd.read_csv(io.StringIO(response.text), index_col=0, parse_dates=True).iloc[:, 0]
        us_vix, us_source = pd.to_numeric(fred, errors="coerce").dropna(), "FRED VIXCLS"
    except Exception:
        us_vix, us_source = closes_of("^VIX"), "Yahoo Finance ^VIX (FRED was unreachable)"

    return {
        "india_vix": standing(india_vix),
        "us_vix": {**standing(us_vix), "source": us_source},
        "implied_over_realised": {
            "median": round(float(ratio.median()), 2),
            "share_of_days_implied_was_higher": round(float((ratio > 1).mean()), 3),
            "days": int(len(ratio)),
            "meaning": "Above 1 means options priced in more volatility than the Nifty then delivered.",
        },
        "put_cost_method": "Black-Scholes at-the-money put, India VIX as volatility, zero interest rate, "
                           f"{HORIZON} sessions to expiry. An estimate, not a quote.",
    }


def main() -> None:
    """Train everything, or only the models named on the command line."""
    only = set(sys.argv[1:])
    TRAINED_DIR.mkdir(parents=True, exist_ok=True)
    # Power assets come before storm impact, which reads them.
    for name, build in (("shock_model", train_shock_model), ("sentiment_eval", evaluate_sentiment),
                        ("concentration_benchmark", concentration_benchmark),
                        ("power_assets", build_power_assets), ("storm_impact", train_storm_impact),
                        ("hedge_cost", hedge_cost_stats), ("impact_model", train_impact_model)):
        if only and name not in only:
            continue
        print(f"\n=== {name}")
        try:
            result = build()
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            continue
        # The trees are long arrays of numbers, so that file is written without indentation.
        layout = {"separators": (",", ":")} if "models" in result else {"indent": 2}
        (TRAINED_DIR / f"{name}.json").write_text(json.dumps(result, **layout) + "\n")
        assets.load_assets.cache_clear()
        summary = {k: v for k, v in result.items()
                   if k not in ("mean", "std", "percentiles", "plants", "library", "models")}
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
