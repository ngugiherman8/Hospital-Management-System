from django.urls import path

from . import views

urlpatterns = [
    path("lab/", views.worklist, name="lab_worklist"),
    path("lab/item/<int:pk>/sample/", views.sample_taken, name="sample_taken"),
    path("lab/item/<int:pk>/result/", views.result_entry, name="result_entry"),
    path("visit/<int:pk>/order/", views.order_create, name="order_create"),
    path("item/<int:pk>/cancel/", views.item_cancel, name="item_cancel"),
]
