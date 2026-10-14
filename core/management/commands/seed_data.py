import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from core.models import Product, Dealer
class Command(BaseCommand):
    help="Idempotently load catalogue and dealers from data/"
    def handle(self,*args,**options):
        with (settings.DATA_DIR/"catalogue.csv").open(newline="",encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                Product.objects.update_or_create(sku=r["sku"],defaults={"name":r["name"],"category":r["category"],"brand":r["brand"],"vehicle_fitment":r["vehicle_fitment"],"price":Decimal(r["price"]),"stock":int(r["stock"]),"description":r.get("description","")})
        with (settings.DATA_DIR/"dealers.csv").open(newline="",encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                Dealer.objects.update_or_create(dealer_code=r["dealer_code"],defaults={"name":r["name"],"city":r.get("city",""),"phone":r.get("phone",""),"email":r.get("email",""),"gstin":r.get("gstin",""),"credit_limit":Decimal(r.get("credit_limit") or "0")})
        self.stdout.write(self.style.SUCCESS(f"Seeded {Product.objects.count()} products and {Dealer.objects.count()} dealers"))
