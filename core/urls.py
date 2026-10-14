from django.urls import path
from . import views, views_extra
urlpatterns=[
 path("health/",views.health),
 path("products/",views.products),path("products/<str:sku>/",views.product_detail),
 path("inventory/<str:sku>/",views.inventory),
 path("dealers/",views.dealers),path("dealers/<int:pk>/",views.dealer_detail),
 path("orders/",views.orders),path("orders/<str:no>/",views.order_detail),
 path("orders/<str:no>/confirm/",views.order_action,{"action":"confirm"}),
 path("orders/<str:no>/dispatch/",views.order_action,{"action":"dispatch"}),
 path("orders/<str:no>/deliver/",views.order_action,{"action":"deliver"}),
 path("orders/<str:no>/cancel/",views.order_action,{"action":"cancel"}),
 path("sync/supplier/",views.supplier_sync),
 path("assistant/chat/",views_extra.assistant_chat),
 path("forecast/<str:sku>/",views_extra.forecast_detail),
 path("reorder-suggestions/",views_extra.reorder_suggestions),
]
