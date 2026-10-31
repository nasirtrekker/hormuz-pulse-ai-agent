"""Public Brent history (FRED series DCOILBRENTEU, daily, USD per barrel, from 1987).

A snapshot ships with the app so it works offline; refresh_snapshot() re-downloads it.
"""
import csv
import pathlib
import urllib.request

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"
SNAPSHOT = pathlib.Path(__file__).parent / "data" / "brent_fred_dcoilbrenteu.csv"


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


if __name__ == "__main__":
    print(f"{refresh_snapshot()} observations written to {SNAPSHOT}")
