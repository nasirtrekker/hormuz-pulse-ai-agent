"""Public Brent history (FRED series DCOILBRENTEU, daily, USD per barrel, from 1987).

A snapshot ships with the app so it works offline; refresh_snapshot() re-downloads it.
"""
import csv
import pathlib
import urllib.request

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
