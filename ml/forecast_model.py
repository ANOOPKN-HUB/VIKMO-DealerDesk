"""Reproducible four-week forecast and leakage-safe rolling-origin evaluation.

Run from the repository root:
    python ml/forecast_model.py --evaluate
    python ml/forecast_model.py --write

Only sales observed on or before each rolling cutoff are used to fit a fold. Planned
promotion flags and the festival calendar are treated as known future inputs.
"""

import argparse
import csv
import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "data" / "sales_history.csv"
PROMO_FILE = ROOT / "data" / "promo_calendar.csv"
LEAD_FILE = ROOT / "data" / "lead_times.csv"
FESTIVAL_FILE = ROOT / "data" / "festival_calendar.csv"
OUTPUT_FILE = ROOT / "ml" / "forecast.csv"
HORIZON_WEEKS = 4
RECENT_WEEKS = 8


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def load_inputs():
    history = defaultdict(list)
    by_sku_week = {}
    for row in read_csv(HISTORY_FILE):
        sku = row["sku"]
        week = date.fromisoformat(row["week_start"])
        record = {"week": week, "units": float(row["units_sold"]), "promo": int(row["promo_flag"])}
        history[sku].append(record)
        by_sku_week[(sku, week)] = record
    for rows in history.values():
        rows.sort(key=lambda row: row["week"])

    leads = {
        row["sku"]: int(row["lead_time_weeks"])
        for row in read_csv(LEAD_FILE)
    }
    promotions = {
        (row["sku"], date.fromisoformat(row["week_start"])): int(row["promo_flag"])
        for row in read_csv(PROMO_FILE)
    }
    festivals = [
        date.fromisoformat(row["date"])
        for row in read_csv(FESTIVAL_FILE)
        if row["festival"].strip().lower() == "diwali"
    ]
    return history, by_sku_week, leads, promotions, festivals


def _history_before(rows, cutoff):
    return [row for row in rows if row["week"] <= cutoff]


def _recent_mean(rows):
    if not rows:
        return 0.0
    return statistics.fmean(row["units"] for row in rows[-RECENT_WEEKS:])


def _promotion_ratio(history, cutoff):
    promo_values = []
    regular_values = []
    for rows in history.values():
        for row in rows:
            if row["week"] <= cutoff:
                (promo_values if row["promo"] else regular_values).append(row["units"])
    if len(promo_values) < 8 or len(regular_values) < 8:
        return 1.0
    regular_mean = statistics.fmean(regular_values)
    if regular_mean <= 0:
        return 1.0
    return min(1.5, max(1.0, statistics.fmean(promo_values) / regular_mean))


def _near_diwali(target_week, festivals):
    return any(7 <= (festival - target_week).days <= 35 for festival in festivals)


def predict(sku, target_week, cutoff, history, by_sku_week, promotions, festivals, method):
    observed = _history_before(history.get(sku, []), cutoff)
    recent = _recent_mean(observed)
    seasonal_row = by_sku_week.get((sku, target_week - timedelta(days=364)))
    seasonal = seasonal_row["units"] if seasonal_row and seasonal_row["week"] <= cutoff else recent

    if method == "last_value":
        estimate = observed[-1]["units"] if observed else 0.0
    elif method == "recent_average":
        estimate = recent
    elif method == "same_week_last_year":
        estimate = seasonal
    elif method in {"blend", "event_adjusted_blend"}:
        estimate = 0.6 * recent + 0.4 * seasonal
        if method == "event_adjusted_blend":
            if promotions.get((sku, target_week), 0):
                estimate *= _promotion_ratio(history, cutoff)
            if _near_diwali(target_week, festivals):
                estimate *= 1.05
    else:
        raise ValueError(f"Unknown forecast method: {method}")
    return max(0.0, estimate)


METHODS = (
    "last_value",
    "recent_average",
    "same_week_last_year",
    "blend",
    "event_adjusted_blend",
)


def rolling_origins(history):
    weeks = sorted({row["week"] for rows in history.values() for row in rows})
    # Give seasonal-naive a full year of prior observations, then advance by 8 weeks.
    return [weeks[index] for index in range(52, len(weeks) - HORIZON_WEEKS, 8)]


def _score(predictions):
    if not predictions:
        return {"mae": 0.0, "wmape": 0.0, "n": 0}
    absolute_errors = [abs(actual - predicted) for actual, predicted in predictions]
    denominator = sum(actual for actual, _ in predictions)
    wmape = sum(absolute_errors) / denominator if denominator else 0.0
    return {
        "mae": statistics.fmean(absolute_errors),
        "wmape": wmape,
        "n": len(predictions),
    }


def evaluate(history, by_sku_week, promotions, festivals, origins):
    if len(origins) < 4:
        raise ValueError("At least four rolling origins are needed for a tune/validation split")
    split = max(1, len(origins) // 2)
    tune_origins, validation_origins = origins[:split], origins[split:]

    def fold_scores(folds):
        predictions = {method: [] for method in METHODS}
        for cutoff in folds:
            targets = [cutoff + timedelta(days=7 * step) for step in range(1, HORIZON_WEEKS + 1)]
            for (sku, week), actual_row in by_sku_week.items():
                if week not in targets:
                    continue
                actual = actual_row["units"]
                for method in METHODS:
                    value = predict(sku, week, cutoff, history, by_sku_week, promotions, festivals, method)
                    predictions[method].append((actual, value))
        return {method: _score(values) for method, values in predictions.items()}

    tune = fold_scores(tune_origins)
    selected = min(METHODS, key=lambda method: (tune[method]["wmape"], tune[method]["mae"]))
    validation = fold_scores(validation_origins)
    return selected, tune, validation, tune_origins, validation_origins


def write_forecast(history, by_sku_week, leads, promotions, festivals, method, cutoff):
    start = cutoff + timedelta(days=7)
    weeks = [start + timedelta(days=7 * offset) for offset in range(HORIZON_WEEKS)]
    rows = []
    for sku in sorted(leads):
        for week in weeks:
            value = predict(sku, week, cutoff, history, by_sku_week, promotions, festivals, method)
            rows.append({"sku": sku, "week_start": week.isoformat(), "forecast_units": f"{value:.1f}"})
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["sku", "week_start", "forecast_units"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows), weeks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluate", action="store_true", help="run rolling-origin comparison")
    parser.add_argument("--write", action="store_true", help="write ml/forecast.csv using the selected method")
    args = parser.parse_args()
    if not args.evaluate and not args.write:
        args.evaluate = True

    history, by_sku_week, leads, promotions, festivals = load_inputs()
    origins = rolling_origins(history)
    selected, tune, validation, tune_origins, validation_origins = evaluate(
        history, by_sku_week, promotions, festivals, origins
    )
    if args.evaluate:
        print(f"Rolling-origin cutoffs: tune={len(tune_origins)}, validation={len(validation_origins)}")
        print(f"Tune cutoffs: {', '.join(day.isoformat() for day in tune_origins)}")
        print(f"Validation cutoffs: {', '.join(day.isoformat() for day in validation_origins)}")
        print(f"Selected on tune folds: {selected}")
        print("Validation metrics (lower is better):")
        for method in METHODS:
            score = validation[method]
            print(f"  {method:24s} MAE={score['mae']:.3f} WMAPE={score['wmape']:.4f} n={score['n']}")
    if args.write:
        cutoff = max(row["week"] for rows in history.values() for row in rows)
        count, weeks = write_forecast(history, by_sku_week, leads, promotions, festivals, selected, cutoff)
        print(f"Wrote {count} forecasts with {selected}, cutoff={cutoff.isoformat()}, weeks={weeks[0]}..{weeks[-1]}")


if __name__ == "__main__":
    main()
