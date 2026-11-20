from django.urls import path

from . import views

urlpatterns = [
    path("visit/<int:pk>/", views.dental_visit, name="dental_visit"),
    path("visit/<int:pk>/save/", views.visit_save, name="dental_visit_save"),
    path("visit/<int:pk>/tooth/", views.tooth_add, name="tooth_add"),
    path("visit/<int:pk>/plan/item/", views.plan_item_add, name="plan_item_add"),
    path("visit/<int:pk>/plan/<int:plan_pk>/consent/", views.plan_consent, name="plan_consent"),
    path("visit/<int:pk>/plan/item/<int:item_pk>/action/", views.plan_item_action, name="plan_item_action"),
]
