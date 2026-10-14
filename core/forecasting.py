import csv
import math
import statistics
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from django.conf import settings

WEEKS = ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"]


class ForecastDataError(ValueError):
    """Raised when the committed forecast cannot safely drive an API response."""


def _read_rows(path):
    try:
        with path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            return reader.fieldnames, list(reader)
    except OSError as exc:
        raise ForecastDataError("Forecast input file is unavailable") from exc


def forecast_rows():
    path = settings.BASE_DIR / "ml" / "forecast.csv"
    fields, rows = _read_rows(path)
    if fields != ["sku", "week_start", "forecast_units"]:
        raise ForecastDataError("Forecast columns do not match the required format")

    lead_fields, lead_rows = _read_rows(settings.DATA_DIR / "lead_times.csv")
    if not lead_fields or not {"sku", "lead_time_weeks"}.issubset(lead_fields):
        raise ForecastDataError("Lead-time data is invalid")
    expected_skus = {row.get("sku", "").strip() for row in lead_rows}
    if "" in expected_skus or not expected_skus:
        raise ForecastDataError("Lead-time data is invalid")
    expected = {(sku, week) for sku in expected_skus for week in WEEKS}

    seen = set()
    normalized = []
    for row in rows:
        sku = (row.get("sku") or "").strip()
        week = (row.get("week_start") or "").strip()
        try:
            value = Decimal((row.get("forecast_units") or "").strip())
        except (InvalidOperation, AttributeError):
            raise ForecastDataError("Forecast contains a non-numeric value")
        if not sku or week not in WEEKS or not value.is_finite() or value < 0:
            raise ForecastDataError("Forecast contains an invalid SKU, week, or quantity")
        key = (sku, week)
        if key in seen:
            raise ForecastDataError("Forecast contains a duplicate SKU and week")
        seen.add(key)
        normalized.append({"sku": sku, "week_start": week, "forecast_units": value})
    if seen != expected:
        raise ForecastDataError("Forecast must contain one row for every required SKU and week")
    return normalized


def reorder_data():
    rows = forecast_rows()
    grouped = {}
    for row in rows:
        grouped.setdefault(row["sku"], []).append(row)

    _, lead_rows = _read_rows(settings.DATA_DIR / "lead_times.csv")
    lead = {}
    for row in lead_rows:
        try:
            weeks = int(row["lead_time_weeks"])
        except (KeyError, TypeError, ValueError):
            raise ForecastDataError("Lead-time data contains an invalid value")
        if weeks < 1 or weeks > 4:
            raise ForecastDataError("Lead time must be between one and four weeks")
        lead[row["sku"]] = weeks

    _, history_rows = _read_rows(settings.DATA_DIR / "sales_history.csv")
    history = {}
    for row in history_rows:
        try:
            value = float(row["units_sold"])
            week = row["week_start"]
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            history.setdefault(row.get("sku", ""), []).append((week, value))

    from .models import Product

    products = Product.objects.in_bulk(lead.keys(), field_name="sku")
    out = []
    for sku, lead_time_weeks in lead.items():
        forecasts = sorted(grouped[sku], key=lambda row: row["week_start"])
        lead_time_demand = sum(
            (row["forecast_units"] for row in forecasts[:lead_time_weeks]),
            Decimal("0"),
        )
        recent_values = [value for _, value in sorted(history.get(sku, []))[-12:]]
        sigma = statistics.stdev(recent_values) if len(recent_values) > 1 else 0.0
        safety_stock = math.ceil(1.65 * sigma * math.sqrt(lead_time_weeks))
        current_stock = products[sku].stock if sku in products else 0
        reorder_point_exact = lead_time_demand + Decimal(safety_stock)
        reorder_quantity = int((reorder_point_exact - Decimal(current_stock)).to_integral_value(rounding=ROUND_CEILING))
        out.append({
            "sku": sku,
            "current_stock": current_stock,
            "lead_time_weeks": lead_time_weeks,
            "lead_time_demand": round(float(lead_time_demand), 4),
            "safety_stock": safety_stock,
            "reorder_point": round(float(reorder_point_exact), 4),
            "reorder_qty": max(0, reorder_quantity),
        })
    return out
