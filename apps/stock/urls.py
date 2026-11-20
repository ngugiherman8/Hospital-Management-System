from django.urls import path

from . import views

urlpatterns = [
    path("", views.levels, name="stock_levels"),
    path("dispense/", views.dispense_queue, name="dispense_queue"),
    path("dispense/<int:pk>/", views.dispense_detail, name="dispense_detail"),
    path("dispense/<int:pk>/item/<int:item_pk>/", views.dispense_item, name="dispense_item"),
    path("receipts/", views.receipt_list, name="receipt_list"),
    path("receipts/new/", views.receipt_create, name="receipt_create"),
    path("receipts/<int:pk>/", views.receipt_detail, name="receipt_detail"),
    path("orders/", views.order_list, name="order_list"),
    path("orders/new/", views.order_create, name="order_create"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/<str:action>/", views.order_status, name="order_status"),
    path("suppliers/", views.supplier_list, name="supplier_list"),
    path("adjust/", views.adjust, name="stock_adjust"),
    path("take/", views.take, name="stock_take"),
]
