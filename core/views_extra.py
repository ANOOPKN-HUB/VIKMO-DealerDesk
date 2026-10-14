import logging

from rest_framework.decorators import api_view
from rest_framework.response import Response
from .assistant import run_assistant
from .forecasting import ForecastDataError, forecast_rows, reorder_data

logger = logging.getLogger(__name__)


@api_view(["POST"])
def assistant_chat(request):
    try:
        result = run_assistant(request.data)
    except Exception:
        logger.exception("Unhandled assistant request failure")
        return Response(
            {
                "error": {
                    "code": "ASSISTANT_ERROR",
                    "message": "The assistant couldn't complete the request. Please try again.",
                    "details": [],
                }
            },
            status=500,
        )
    return Response(result)
@api_view(["GET"])
def forecast_detail(request,sku):
    try:
        rows=[r for r in forecast_rows() if r["sku"]==sku]
    except ForecastDataError:
        return Response({"error":{"code":"FORECAST_UNAVAILABLE","message":"Forecast data is invalid or unavailable","details":[]}},status=503)
    if len(rows)!=4:return Response({"error":{"code":"NOT_FOUND","message":"Forecast SKU not found","details":[]}},status=404)
    return Response({"sku":sku,"forecast":[{"week_start":r["week_start"],"forecast_units":float(r["forecast_units"])} for r in sorted(rows,key=lambda x:x["week_start"])]})
@api_view(["GET"])
def reorder_suggestions(request):
    try:
        return Response({"results":reorder_data()})
    except ForecastDataError:
        return Response({"error":{"code":"FORECAST_UNAVAILABLE","message":"Forecast data is invalid or unavailable","details":[]}},status=503)
