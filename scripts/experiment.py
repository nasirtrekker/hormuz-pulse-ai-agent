"""Do news-based and market-implied features improve the 2-trading-day Brent forecast?

Walk-forward: models are refit every ~year using only data whose outcome was known; scored
on 2015 onward with the pinball (quantile) loss, which is minimised only by true quantiles.
"""
import math
import sys
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

warnings.filterwarnings("ignore")
D = "hormuz/data/"
QS = (0.1, 0.5, 0.9)


def series(file, col):
    s = pd.read_csv(D + file, na_values=["."]).set_index("observation_date" if "fred" in file or "brent" in file else "date")[col]
    s.index = pd.to_datetime(s.index)
    return pd.to_numeric(s, errors="coerce")


brent = series("brent_fred_dcoilbrenteu.csv", "DCOILBRENTEU").dropna()
brent = brent[brent > 0]
df = pd.DataFrame({"p": brent})
df["ovx"] = series("ovx_fred.csv", "OVXCLS")
df["vix"] = series("vix_fred.csv", "VIXCLS")
df["usd"] = series("usd_fred.csv", "DTWEXBGS")
g = pd.read_csv(D + "gpr_daily.csv", parse_dates=["date"]).set_index("date")
for c in ("GPRD", "GPRD_ACT", "GPRD_THREAT"):
    df[c] = g[c].reindex(df.index).shift(1)  # news index lagged one day: no same-day look-ahead
df = df.ffill(limit=5)
df = df[df.index >= "2007-06-01"].dropna().copy()

lp = np.log(df["p"])
r1 = lp.diff()
df["sig20"] = r1.rolling(20).std()
df["r1"], df["r5"], df["r20"] = r1, lp.diff(5), lp.diff(20)
df["ovx_d"] = df["ovx"] / 100 / math.sqrt(252)               # market-implied daily vol
df["ovx_chg5"] = np.log(df["ovx"]).diff(5)
df["gpr"] = np.log(df["GPRD"])
df["gpr_ratio"] = df["GPRD"].rolling(7).mean() / df["GPRD"].rolling(30).mean()
df["gpr_threat"] = np.log(df["GPRD_THREAT"].rolling(7).mean())
df["vix_l"] = np.log(df["vix"])
df["usd5"] = np.log(df["usd"]).diff(5)
df["blend"] = np.sqrt(df["sig20"] * df["ovx_d"])  # geometric mean of realized and implied vol
df["y"] = lp.shift(-2) - lp
latest = df.iloc[[-1]].copy()  # today's features: no outcome yet
df = df.dropna(subset=["sig20", "r20", "ovx_chg5", "gpr_ratio", "usd5", "y"])
FEAT_MKT = ["r1", "r5", "r20", "sig20", "ovx_d", "ovx_chg5", "vix_l", "usd5"]
FEAT_NEWS = ["gpr", "gpr_ratio", "gpr_threat"]


def pinball(y, qpred, q):
    d = y - qpred
    return np.mean(np.maximum(q * d, (q - 1) * d))


def empirical(train, scale_col, test):
    z = (train["y"] / (train[scale_col] * math.sqrt(2))).values
    return {q: np.quantile(z, q) * test[scale_col].values * math.sqrt(2) for q in QS}


def gbm(train, feats, test, scale_col="sig20"):
    out = {}
    z = train["y"] / (train[scale_col] * math.sqrt(2))
    for q in QS:
        m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_depth=3, max_iter=120,
                                          learning_rate=0.04, min_samples_leaf=60, l2_regularization=5.0)
        m.fit(train[feats], z)
        out[q] = m.predict(test[feats]) * test[scale_col].values * math.sqrt(2)
    return out


MODELS = {
    "A realized-vol empirical (current app)": lambda tr, te: empirical(tr, "sig20", te),
    "B implied-vol (OVX) empirical": lambda tr, te: empirical(tr, "ovx_d", te),
    "B2 blend realized+implied vol empirical": lambda tr, te: empirical(tr, "blend", te),
    "C GBM market features": lambda tr, te: gbm(tr, FEAT_MKT, te),
    "D GBM market + news (GPR)": lambda tr, te: gbm(tr, FEAT_MKT + FEAT_NEWS, te),
    "E GBM market + news, implied-vol scale": lambda tr, te: gbm(tr, FEAT_MKT + FEAT_NEWS, te, "ovx_d"),
}

years = range(2015, int(df.index[-1].year) + 1)
store = {k: {q: [] for q in QS} for k in MODELS}
ys, rw = [], []
for yr in years:
    test = df[(df.index.year == yr)].iloc[::2]  # every 2nd day: non-overlapping 2-day windows
    train = df[df.index < f"{yr}-01-01"].iloc[:-2]  # drop rows whose target is not yet known
    if len(test) == 0:
        continue
    ys.append(test["y"].values)
    for name, fn in MODELS.items():
        pred = fn(train, test)
        for q in QS:
            store[name][q].append(pred[q])
y = np.concatenate(ys)
results = {"test_points": int(len(y)), "period": f"{years[0]} to {df.index[-1].date()}", "models": [], "news_tests": []}
print(f"test points: {len(y)} (2-day returns, {years[0]} to {df.index[-1].date()})\n")
print(f"{'model':45s} {'pinball':>9s} {'vs A':>7s} {'cover80':>8s} {'width%':>7s} {'MAE-med%':>9s}")
base = None
for name in MODELS:
    P = {q: np.concatenate(store[name][q]) for q in QS}
    loss = np.mean([pinball(y, P[q], q) for q in QS])
    base = base or loss
    cover = np.mean((y >= P[0.1]) & (y <= P[0.9])) * 100
    results["models"].append({"name": name, "pinball": round(loss * 1e4, 2), "vs_current_pct": round(100 * (loss / base - 1), 1),
                              "coverage80_pct": round(float(cover), 1), "width_pct": round(float(100 * np.mean(P[0.9] - P[0.1])), 2),
                              "median_abs_error_pct": round(float(100 * np.mean(np.abs(y - P[0.5]))), 3)})
    print(f"{name:45s} {loss*1e4:9.2f} {100*(loss/base-1):+6.1f}% {cover:8.1f} {100*np.mean(P[0.9]-P[0.1]):7.2f} {100*np.mean(np.abs(y-P[0.5])):9.3f}")
print(f"{'no-change point forecast (reference)':45s} {'':9s} {'':7s} {'':8s} {'':7s} {100*np.mean(np.abs(y)):9.3f}")

from scipy.stats import spearmanr
print("\nDirect test on the full sample (Spearman rank correlation with the NEXT 2-day move):")
for f in ("gpr", "gpr_ratio", "gpr_threat", "ovx_chg5", "r5"):
    rd, pd_ = spearmanr(df[f], df["y"]); ra, pa = spearmanr(df[f], df["y"].abs() / df["sig20"])
    results["news_tests"].append({"feature": f, "rho_direction": round(float(rd), 3), "p_direction": round(float(pd_), 3),
                                  "rho_size": round(float(ra), 3), "p_size": round(float(pa), 3)})
    print(f"  {f:11s} vs direction: rho={rd:+.3f} (p={pd_:.2f})   vs size of move/vol: rho={ra:+.3f} (p={pa:.3f})")

results["no_change_median_abs_error_pct"] = round(float(100 * np.mean(np.abs(y))), 3)
import json
json.dump(results, open(D + "experiment_results.json", "w"), indent=1)
print("wrote", D + "experiment_results.json")
