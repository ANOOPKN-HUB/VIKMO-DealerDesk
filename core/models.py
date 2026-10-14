from django.db import models
class Product(models.Model):
    sku=models.CharField(max_length=80, unique=True)
    name=models.CharField(max_length=255); category=models.CharField(max_length=100, blank=True)
    brand=models.CharField(max_length=100, blank=True); vehicle_fitment=models.CharField(max_length=255, blank=True)
    price=models.DecimalField(max_digits=12, decimal_places=2); description=models.TextField(blank=True)
    stock=models.PositiveIntegerField(default=0)
    def __str__(self): return self.sku
class Dealer(models.Model):
    dealer_code=models.CharField(max_length=80, unique=True); name=models.CharField(max_length=255)
    city=models.CharField(max_length=100, blank=True); phone=models.CharField(max_length=40, blank=True)
    email=models.EmailField(blank=True); gstin=models.CharField(max_length=40, blank=True)
    credit_limit=models.DecimalField(max_digits=12,decimal_places=2,default=0)
class Order(models.Model):
    order_number=models.CharField(max_length=32,unique=True)
    dealer=models.ForeignKey(Dealer,on_delete=models.PROTECT,related_name="orders")
    status=models.CharField(max_length=20,default="DRAFT")
    subtotal=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    tax_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    grand_total=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    created_at=models.DateTimeField(auto_now_add=True); updated_at=models.DateTimeField(auto_now=True)
class OrderItem(models.Model):
    order=models.ForeignKey(Order,on_delete=models.CASCADE,related_name="items")
    product=models.ForeignKey(Product,on_delete=models.PROTECT)
    quantity=models.PositiveIntegerField()
    unit_price=models.DecimalField(max_digits=12,decimal_places=2)
    line_subtotal=models.DecimalField(max_digits=14,decimal_places=2)
    line_tax=models.DecimalField(max_digits=14,decimal_places=2)
    line_total=models.DecimalField(max_digits=14,decimal_places=2)
class IdempotencyRecord(models.Model):
    key=models.CharField(max_length=255,unique=True); body_hash=models.CharField(max_length=64)
    order=models.ForeignKey(Order,on_delete=models.CASCADE)
