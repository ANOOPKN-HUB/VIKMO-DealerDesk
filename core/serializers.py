from decimal import Decimal, ROUND_HALF_UP
from rest_framework import serializers
from .models import Product, Dealer, Order, OrderItem
def money(v): return str(Decimal(v).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))
class ProductSerializer(serializers.ModelSerializer):
    price=serializers.DecimalField(max_digits=12,decimal_places=2,coerce_to_string=True)
    stock=serializers.IntegerField(read_only=True)
    class Meta: model=Product; fields=["sku","name","category","brand","vehicle_fitment","price","stock","description"]

    def validate_price(self, value):
        if value <= 0:
            raise serializers.ValidationError("Price must be greater than zero.")
        return value
class DealerSerializer(serializers.ModelSerializer):
    credit_limit=serializers.DecimalField(max_digits=12,decimal_places=2,coerce_to_string=True,required=False)
    class Meta: model=Dealer; fields=["id","dealer_code","name","city","phone","email","gstin","credit_limit"]
class OrderItemSerializer(serializers.ModelSerializer):
    sku=serializers.CharField(source="product.sku"); name=serializers.CharField(source="product.name")
    unit_price=serializers.DecimalField(max_digits=12,decimal_places=2,coerce_to_string=True)
    line_subtotal=serializers.DecimalField(max_digits=14,decimal_places=2,coerce_to_string=True)
    line_tax=serializers.DecimalField(max_digits=14,decimal_places=2,coerce_to_string=True)
    line_total=serializers.DecimalField(max_digits=14,decimal_places=2,coerce_to_string=True)
    class Meta: model=OrderItem; fields=["sku","name","quantity","unit_price","line_subtotal","line_tax","line_total"]
class OrderSerializer(serializers.ModelSerializer):
    dealer_id=serializers.IntegerField(source="dealer.id"); items=OrderItemSerializer(many=True,read_only=True)
    subtotal=serializers.DecimalField(max_digits=14,decimal_places=2,coerce_to_string=True)
    tax_total=serializers.DecimalField(max_digits=14,decimal_places=2,coerce_to_string=True)
    grand_total=serializers.DecimalField(max_digits=14,decimal_places=2,coerce_to_string=True)
    class Meta: model=Order; fields=["order_number","dealer_id","status","items","subtotal","tax_total","grand_total","created_at","updated_at"]
