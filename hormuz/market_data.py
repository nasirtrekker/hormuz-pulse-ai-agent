"""Public Brent history (FRED series DCOILBRENTEU, daily, USD per barrel, from 1987).

A snapshot ships with the app so it works offline; refresh_snapshot() re-downloads it.
"""
import csv
import json
import pathlib
import threading
import time
import urllib.request
from datetime import datetime, timezone

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
OVX_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=OVXCLS"
DATA_DIR = pathlib.Path(__file__).parent / "data"
SNAPSHOT = DATA_DIR / "brent_fred_dcoilbrenteu.csv"
OVX_SNAPSHOT = DATA_DIR / "ovx_fred.csv"


def parse(text: str) -> list[tuple[str, float]]:
    """Return (date, price) rows, skipping FRED's missing-value markers and non-positive prices."""
    rows = []
    for row in csv.DictReader(text.splitlines()):
        try:
            price = float(row["DCOILBRENTEU"])
        except (ValueError, KeyError, TypeError):
            continue
        if price > 0:
            rows.append((row["observation_date"], price))
    return rows


def parse_ovx(text: str) -> list[tuple[str, float]]:
    """CBOE crude oil volatility index (annualised implied vol, in %), as (date, value)."""
    rows = []
    for row in csv.DictReader(text.splitlines()):
        try:
            value = float(row["OVXCLS"])
        except (ValueError, KeyError, TypeError):
            continue
        if value > 0:
            rows.append((row["observation_date"], value))
    return rows


def load_ovx(path: pathlib.Path = OVX_SNAPSHOT) -> list[tuple[str, float]]:
    return parse_ovx(path.read_text())


def align_implied_vol(dates, ovx_rows, max_gap_days: int = 5):
    """Daily implied vol (OVX/100/sqrt(252)) on the Brent dates, carrying a value forward up to
    max_gap_days. Returns (aligned list with NaN where unknown, latest implied vol)."""
    import math
    from datetime import date
    by_date = {d: v for d, v in ovx_rows}
    out, last_value, last_day = [], None, None
    for d in dates:
        if d in by_date:
            last_value, last_day = by_date[d], date.fromisoformat(d)
        stale = last_day is None or (date.fromisoformat(d) - last_day).days > max_gap_days
        out.append(float("nan") if last_value is None or stale else last_value / 100 / math.sqrt(252))
    latest = ovx_rows[-1][1] / 100 / math.sqrt(252)
    return out, latest


def load_history(path: pathlib.Path = SNAPSHOT) -> list[tuple[str, float]]:
    return parse(path.read_text())


def refresh_snapshot(path: pathlib.Path = SNAPSHOT) -> int:
    with urllib.request.urlopen(FRED_URL, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    rows = parse(text)
    if len(rows) < 5000:
        raise RuntimeError("download looks incomplete; snapshot not replaced")
    path.write_text(text)
    return len(rows)


def refresh_ovx_snapshot(path: pathlib.Path = OVX_SNAPSHOT) -> int:
    with urllib.request.urlopen(OVX_URL, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    rows = parse_ovx(text)
    if len(rows) < 3000:
        raise RuntimeError("OVX download looks incomplete; snapshot not replaced")
    path.write_text(text)
    return len(rows)


if __name__ == "__main__":
    print(f"{refresh_snapshot()} Brent observations written to {SNAPSHOT}")
    print(f"{refresh_ovx_snapshot()} OVX observations written to {OVX_SNAPSHOT}")


FUTURES_URL = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?range=2y&interval=1d"
_futures_cache: dict = {"at": 0.0, "value": None}
_futures_lock = threading.Lock()


def parse_futures_series(text: str, reference: float) -> list:
    """Daily closes of front-month Brent futures (BZ=F) as [(date, price)], or [] if implausible.

    The latest close must be finite, within 10..500 USD and within 35% of the reference spot
    price, so a bad feed can never move the anchor far.
    """
    result = json.loads(text)["chart"]["result"][0]
    rows = [(datetime.fromtimestamp(ts, timezone.utc).date().isoformat(), round(float(c), 2))
            for ts, c in zip(result["timestamp"], result["indicators"]["quote"][0]["close"])
            if c is not None and 10 <= float(c) <= 500]
    if not rows or abs(rows[-1][1] / reference - 1) > 0.35:
        return []
    return rows


def parse_futures(text: str, reference: float):
    rows = parse_futures_series(text, reference)
    return rows[-1] if rows else None


def futures_series(reference: float, ttl: int = 900) -> list:
    """Cached futures closes, or [] when the unofficial public feed is unavailable."""
    with _futures_lock:
        if time.time() - _futures_cache["at"] < ttl:
            return _futures_cache["value"] or []
        _futures_cache["at"] = time.time()
        try:
            req = urllib.request.Request(FUTURES_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                _futures_cache["value"] = parse_futures_series(resp.read().decode("utf-8"), reference)
        except Exception:
            _futures_cache["value"] = []
        return _futures_cache["value"]


def latest_futures(reference: float, ttl: int = 900):
    rows = futures_series(reference, ttl)
    return rows[-1] if rows else None
