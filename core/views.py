import csv, json, os, hashlib, time, math, statistics
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date, datetime, timezone as dt_timezone
from django.conf import settings
from django.db import connection, transaction, IntegrityError
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
import requests

from .models import Product, Dealer, Order, OrderItem, IdempotencyRecord
from .serializers import ProductSerializer, DealerSerializer, OrderSerializer

def err(code,message,details=None,http=400): return Response({"error":{"code":code,"message":message,"details":details or []}},status=http)
def page(request, qs, serializer):
    try:
        n = int(request.GET.get("page", "1"))
        requested_size = int(request.GET.get("page_size", "20"))
    except (TypeError, ValueError):
        return err("VALIDATION_ERROR", "page and page_size must be integers")
    if n < 1 or requested_size < 1:
        return err("VALIDATION_ERROR", "page and page_size must be positive integers")
    size = min(100, requested_size)
    count=qs.count(); start=(n-1)*size
    def url(p): return request.build_absolute_uri(f"{request.path}?page={p}&page_size={size}") if p>=1 and (p-1)*size<count else None
    return Response({"count":count,"next":url(n+1),"previous":url(n-1),"results":serializer(qs[start:start+size],many=True).data})
def health(request): return JsonResponse({"status":"ok"})
@api_view(["GET","POST"])
def products(request):
    if request.method=="GET":
        qs=Product.objects.all().order_by("sku")
        s=request.GET.get("search",""); c=request.GET.get("category"); v=request.GET.get("vehicle")
        if s: qs=qs.filter(Q(sku__icontains=s)|Q(name__icontains=s)|Q(brand__icontains=s))
        if c: qs=qs.filter(category=c)
        if v: qs=qs.filter(vehicle_fitment__icontains=v)
        return page(request,qs,ProductSerializer)
    data=request.data.copy()
    if not data.get("sku") or not data.get("name"): return err("VALIDATION_ERROR","sku and name are required")
    try: price=Decimal(str(data.get("price")))
    except (InvalidOperation, TypeError, ValueError): return err("VALIDATION_ERROR","Invalid price")
    if not price.is_finite() or price<=0 or price.as_tuple().exponent < -2: return err("VALIDATION_ERROR","Price must be positive with at most 2 decimals")
    if Product.objects.filter(sku=data["sku"]).exists(): return err("DUPLICATE","Product SKU already exists",http=409)
    data["stock"]=0
    ser=ProductSerializer(data=data)
    if not ser.is_valid(): return err("VALIDATION_ERROR","Invalid product",ser.errors)
    try:
        with transaction.atomic():
            p=ser.save(stock=0)
    except IntegrityError:
        return err("DUPLICATE","Product SKU already exists",http=409)
    return Response(ProductSerializer(p).data,status=201)
@api_view(["GET","PATCH"])
def product_detail(request,sku):
    try:p=Product.objects.get(sku=sku)
    except Product.DoesNotExist:return err("NOT_FOUND","Product not found",http=404)
    if request.method=="GET": return Response(ProductSerializer(p).data)
    data=request.data.copy(); data.pop("sku",None)
    ser=ProductSerializer(p,data=data,partial=True)
    if not ser.is_valid():return err("VALIDATION_ERROR","Invalid product",ser.errors)
    ser.save();return Response(ProductSerializer(p).data)
@api_view(["PUT"])
def inventory(request,sku):
    try:p=Product.objects.get(sku=sku)
    except Product.DoesNotExist:return err("NOT_FOUND","Product not found",http=404)
    q=request.data.get("quantity")
    if isinstance(q,bool) or not isinstance(q,int) or q<0:return err("VALIDATION_ERROR","quantity must be an integer >= 0")
    p.stock=q;p.save(update_fields=["stock"]);return Response({"sku":sku,"quantity":q})
@api_view(["GET","POST"])
def dealers(request):
    if request.method=="GET": return page(request,Dealer.objects.all().order_by("id"),DealerSerializer)
    data=request.data.copy()
    if not data.get("dealer_code") or not data.get("name"):return err("VALIDATION_ERROR","dealer_code and name are required")
    if Dealer.objects.filter(dealer_code=data["dealer_code"]).exists():return err("DUPLICATE","Dealer code already exists",http=409)
    s=DealerSerializer(data=data)
    if not s.is_valid():return err("VALIDATION_ERROR","Invalid dealer",s.errors)
    try:
        with transaction.atomic():
            dealer=s.save()
    except IntegrityError:
        return err("DUPLICATE","Dealer code already exists",http=409)
    return Response(DealerSerializer(dealer).data,status=201)
@api_view(["GET"])
def dealer_detail(request,pk):
    try:d=Dealer.objects.get(pk=pk)
    except Dealer.DoesNotExist:return err("NOT_FOUND","Dealer not found",http=404)
    return Response(DealerSerializer(d).data)

def calc(items):
    sub=Decimal("0.00"); tax=Decimal("0.00")
    for it in items:
        it.line_subtotal=(it.unit_price*it.quantity).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
        it.line_tax=(it.line_subtotal*Decimal(".18")).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
        it.line_total=it.line_subtotal+it.line_tax; sub+=it.line_subtotal; tax+=it.line_tax
        it.save()
    return sub,tax,sub+tax


def _parse_supplier_updated(value):
    if not value:
        return datetime.min.replace(tzinfo=dt_timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(dt_timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=dt_timezone.utc)


def _supplier_price_is_valid(value):
    if value is None:
        return False
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if not price.is_finite() or price <= 0:
        return False
    if price.as_tuple().exponent < -2:
        return False
    return True
def order_number():
    today = timezone.localdate().strftime("%Y%m%d")
    prefix = f"ORD-{today}-"
    used_numbers = []
    for existing in Order.objects.filter(order_number__startswith=prefix).values_list("order_number", flat=True):
        suffix = existing[len(prefix):]
        if suffix.isdigit():
            used_numbers.append(int(suffix))
    return f"{prefix}{max(used_numbers, default=0) + 1:04d}"


def _advisory_xact_lock(lock_name):
    """Serialize a small critical section across PostgreSQL-backed app workers."""
    if connection.vendor != "postgresql":
        raise RuntimeError("DealerDesk requires PostgreSQL advisory locks")
    lock_id = int.from_bytes(hashlib.sha256(lock_name.encode("utf-8")).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def create_order(data, key=None):
    if not isinstance(data, dict):
        return None, ("VALIDATION_ERROR", "Request body must be a JSON object", []), False
    dealer_id = data.get("dealer_id"); items = data.get("items")
    if not isinstance(items, list) or not items:
        return None, ("VALIDATION_ERROR", "items must be a non-empty list", []), False
    if isinstance(dealer_id, bool) or not isinstance(dealer_id, int):
        return None, ("VALIDATION_ERROR", "dealer_id is required", []), False
    try:
        dealer = Dealer.objects.get(pk=dealer_id)
    except Dealer.DoesNotExist:
        return None, ("VALIDATION_ERROR", "Unknown dealer", []), False
    if key is not None and (not isinstance(key, str) or not key or len(key) > 255):
        return None, ("VALIDATION_ERROR", "Idempotency-Key must contain 1 to 255 characters", []), False
    seen = set(); normalized = []
    for item in items:
        if not isinstance(item, dict):
            return None, ("VALIDATION_ERROR", "Each item must be an object", []), False
        sku = item.get("sku"); q = item.get("quantity")
        if not isinstance(sku, str) or not sku.strip() or isinstance(q, bool) or not isinstance(q, int) or q < 1:
            return None, ("VALIDATION_ERROR", "Each item needs a SKU and integer quantity >= 1", []), False
        sku = sku.strip()
        if sku in seen:
            return None, ("VALIDATION_ERROR", "Duplicate SKU in items", []), False
        seen.add(sku)
        try:
            p = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            return None, ("VALIDATION_ERROR", f"Unknown SKU: {sku}", []), False
        normalized.append((p, q))
    body_hash = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        if key:
            _advisory_xact_lock(f"dealerdesk:idempotency:{key}")
        if key:
            old = IdempotencyRecord.objects.filter(key=key).first()
            if old:
                if old.body_hash != body_hash:
                    return None, ("IDEMPOTENCY_CONFLICT", "Key reused with a different request body", []), False
                return old.order, None, True

        # One per-day lock makes the read/increment/write sequence safe across workers.
        _advisory_xact_lock(f"dealerdesk:order-number:{timezone.localdate().isoformat()}")
        order_number_value = None
        for _ in range(20):
            candidate = order_number()
            try:
                # A savepoint keeps a uniqueness collision from poisoning the outer transaction.
                with transaction.atomic():
                    o = Order.objects.create(order_number=candidate, dealer=dealer)
                order_number_value = candidate
                break
            except IntegrityError:
                continue
        if order_number_value is None:
            raise IntegrityError("Unable to allocate order number")

        for p, q in normalized:
            price = p.price
            sub = (price * q).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            tax = (sub * Decimal(".18")).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
            OrderItem.objects.create(order=o, product=p, quantity=q, unit_price=price, line_subtotal=sub, line_tax=tax, line_total=sub + tax)
        o.subtotal, o.tax_total, o.grand_total = calc(list(o.items.all())); o.save()

        if key:
            try:
                with transaction.atomic():
                    IdempotencyRecord.objects.create(key=key, body_hash=body_hash, order=o)
            except IntegrityError:
                existing = IdempotencyRecord.objects.filter(key=key).first()
                if existing:
                    if existing.body_hash != body_hash:
                        return None, ("IDEMPOTENCY_CONFLICT", "Key reused with a different request body", []), False
                    return existing.order, None, True
                raise
        return o, None, False
@api_view(["GET","POST"])
def orders(request):
    if request.method=="GET":
        qs=Order.objects.all().order_by("-created_at")
        if request.GET.get("dealer_id"):qs=qs.filter(dealer_id=request.GET["dealer_id"])
        if request.GET.get("status"):qs=qs.filter(status=request.GET["status"])
        return page(request,qs,OrderSerializer)
    o,e,is_duplicate=create_order(request.data,request.headers.get("Idempotency-Key"))
    if e:return err(e[0],e[1],e[2],409 if e[0]=="IDEMPOTENCY_CONFLICT" else 400)
    return Response(OrderSerializer(o).data,status=200 if is_duplicate else 201)
@api_view(["GET","PUT"])
def order_detail(request,no):
    try:o=Order.objects.prefetch_related("items__product").get(order_number=no)
    except Order.DoesNotExist:return err("NOT_FOUND","Order not found",http=404)
    if request.method=="GET":return Response(OrderSerializer(o).data)
    if o.status!="DRAFT":return err("ORDER_LOCKED","Only draft orders can be edited",http=409)
    if not isinstance(request.data, dict):
        return err("VALIDATION_ERROR","Request body must be a JSON object")
    data={"dealer_id":o.dealer_id,"items":request.data.get("items")}
    # Validate replacement before mutating
    items=data["items"]
    if not isinstance(items,list) or not items:return err("VALIDATION_ERROR","items must be a non-empty list")
    seen=set(); normalized=[]
    for x in items:
        if not isinstance(x,dict):return err("VALIDATION_ERROR","Each item must be an object")
        sku=x.get("sku");q=x.get("quantity")
        if not isinstance(sku,str) or not sku.strip() or isinstance(q,bool) or not isinstance(q,int) or q<1:return err("VALIDATION_ERROR","Invalid item")
        sku=sku.strip()
        if sku in seen:return err("VALIDATION_ERROR","Invalid or duplicate item")
        seen.add(sku)
        try:p=Product.objects.get(sku=sku)
        except Product.DoesNotExist:return err("VALIDATION_ERROR",f"Unknown SKU: {sku}")
        normalized.append((p,q))
    with transaction.atomic():
        o=Order.objects.select_for_update().get(pk=o.pk)
        if o.status!="DRAFT":return err("ORDER_LOCKED","Only draft orders can be edited",http=409)
        o.items.all().delete()
        for p,q in normalized:
            sub=(p.price*q).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
            tax=(sub*Decimal(".18")).quantize(Decimal(".01"),rounding=ROUND_HALF_UP)
            OrderItem.objects.create(order=o,product=p,quantity=q,unit_price=p.price,line_subtotal=sub,line_tax=tax,line_total=sub+tax)
        o.subtotal,o.tax_total,o.grand_total=calc(list(o.items.all()));o.save()
    return Response(OrderSerializer(o).data)
@api_view(["POST"])
def order_action(request,no,action):
    transitions={"confirm":("DRAFT","CONFIRMED"),"dispatch":("CONFIRMED","DISPATCHED"),"deliver":("DISPATCHED","DELIVERED"),"cancel":(("DRAFT","CONFIRMED"),"CANCELLED")}
    if action not in transitions:return err("NOT_FOUND","Action not found",http=404)
    with transaction.atomic():
        try:o=Order.objects.select_for_update().get(order_number=no)
        except Order.DoesNotExist:return err("NOT_FOUND","Order not found",http=404)
        frm,to=transitions[action]; allowed=frm if isinstance(frm,tuple) else (frm,)
        if o.status not in allowed:return err("INVALID_TRANSITION","Status transition is not allowed",http=409)
        if action=="confirm":
            items=list(o.items.all())
            products={p.pk:p for p in Product.objects.select_for_update().filter(
                pk__in={it.product_id for it in items}
            ).order_by("pk")}
            shortages=[]; shortage_messages=[]
            for it in items:
                p=products[it.product_id]
                if p.stock<it.quantity:
                    shortages.append({"sku":p.sku,"requested":it.quantity,"available":p.stock})
                    shortage_messages.append(
                        f"Insufficient stock for {p.name}. Available: {p.stock}, Requested: {it.quantity}"
                    )
            if shortages:
                return err("INSUFFICIENT_STOCK","; ".join(shortage_messages),shortages,409)
            for it in items:
                p=products[it.product_id];p.stock-=it.quantity;p.save(update_fields=["stock"])
                it.unit_price=p.price;it.line_subtotal=(p.price*it.quantity).quantize(Decimal(".01"),rounding=ROUND_HALF_UP);it.line_tax=(it.line_subtotal*Decimal(".18")).quantize(Decimal(".01"),rounding=ROUND_HALF_UP);it.line_total=it.line_subtotal+it.line_tax;it.save()
            o.subtotal,o.tax_total,o.grand_total=calc(list(o.items.all()))
        if action=="cancel" and o.status=="CONFIRMED":
            items=list(o.items.all())
            products={p.pk:p for p in Product.objects.select_for_update().filter(
                pk__in={it.product_id for it in items}
            ).order_by("pk")}
            for it in items:
                p=products[it.product_id];p.stock+=it.quantity;p.save(update_fields=["stock"])
        o.status=to;o.save()
    return Response(OrderSerializer(o).data)


@api_view(["POST"])
def supplier_sync(request):
    supplier_url = (getattr(settings, "SUPPLIER_API_URL", "") or "").strip().rstrip("/")
    if not supplier_url:
        return err("SUPPLIER_UNAVAILABLE", "Supplier API is not configured", http=502)

    cursor = None
    seen_cursors = set()
    summary = {"created": 0, "updated": 0, "unchanged": 0, "rejected": [], "pages_fetched": 0, "requests_made": 0}
    collected = {}
    url = f"{supplier_url}/v1/products"
    headers = {"X-Api-Key": settings.SUPPLIER_API_KEY or ""}

    while True:
        params = {"limit": 20}
        if cursor is not None:
            params["cursor"] = cursor

        for attempt in range(1, 6):
            summary["requests_made"] += 1
            try:
                response = requests.get(url, params=params, headers=headers, timeout=20)
            except requests.RequestException:
                if attempt >= 5:
                    return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)
                time.sleep(min(2 ** attempt, 5))
                continue

            status = response.status_code
            if status == 401:
                return err("SUPPLIER_UNAVAILABLE", "Supplier API rejected the supplied key", http=502)
            if status == 429:
                retry_after = response.headers.get("Retry-After")
                delay = 1
                try:
                    delay = max(0, float(retry_after)) if retry_after is not None else 1
                except (TypeError, ValueError):
                    delay = 1
                if attempt >= 5:
                    return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)
                time.sleep(delay)
                continue
            if status >= 500:
                if attempt >= 5:
                    return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)
                time.sleep(1 + attempt)
                continue
            if status != 200:
                return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)

            try:
                payload = response.json()
            except ValueError:
                return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)

            items = payload["items"]
            summary["pages_fetched"] += 1
            for item in items:
                if not isinstance(item, dict):
                    summary["rejected"].append({"supplier_sku": None, "reason": "invalid item"})
                    continue
                sku = (item.get("sku") or "").strip()
                if not sku:
                    summary["rejected"].append({"supplier_sku": item.get("supplier_sku"), "reason": "missing sku"})
                    continue
                if not _supplier_price_is_valid(item.get("price")):
                    summary["rejected"].append({"supplier_sku": item.get("supplier_sku"), "reason": "invalid price"})
                    continue
                existing = collected.get(sku)
                updated_at = _parse_supplier_updated(item.get("updated_at"))
                if existing is None or updated_at > existing["updated_at"]:
                    collected[sku] = {
                        "sku": sku,
                        "supplier_sku": item.get("supplier_sku"),
                        "name": (item.get("name") or "").strip(),
                        "category": (item.get("category") or "").strip(),
                        "brand": (item.get("brand") or "").strip(),
                        "vehicle_fitment": (item.get("vehicle_fitment") or "").strip(),
                        "description": (item.get("description") or "").strip(),
                        "price": Decimal(str(item.get("price"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        "updated_at": updated_at,
                    }
            next_cursor = payload.get("next_cursor")
            if next_cursor is not None:
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors or next_cursor == cursor:
                    return err("SUPPLIER_UNAVAILABLE", "Supplier sync returned an invalid pagination cursor", http=502)
                seen_cursors.add(next_cursor)
            if next_cursor is None:
                return _apply_supplier_sync(summary, collected)
            cursor = next_cursor
            break
        else:
            return err("SUPPLIER_UNAVAILABLE", "Supplier sync could not fetch the full feed", http=502)


def _apply_supplier_sync(summary, collected):
    with transaction.atomic():
        product_map = {p.sku: p for p in Product.objects.select_for_update().all()}
        for sku, item in sorted(collected.items()):
            product = product_map.get(sku)
            if product is None:
                Product.objects.create(
                    sku=item["sku"],
                    name=item["name"],
                    category=item["category"],
                    brand=item["brand"],
                    vehicle_fitment=item["vehicle_fitment"],
                    description=item["description"],
                    price=item["price"],
                    stock=0,
                )
                summary["created"] += 1
                continue

            changed = False
            fields = {
                "name": item["name"],
                "category": item["category"],
                "brand": item["brand"],
                "vehicle_fitment": item["vehicle_fitment"],
                "description": item["description"],
                "price": item["price"],
            }
            for field, value in fields.items():
                if getattr(product, field) != value:
                    setattr(product, field, value)
                    changed = True
            if changed:
                product.save(update_fields=list(fields.keys()))
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1

    if summary["created"] + summary["updated"] + summary["unchanged"] == 0:
        summary["created"] = 0; summary["updated"] = 0; summary["unchanged"] = 0
    return Response(summary)
