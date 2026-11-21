from django.urls import path

from . import views

urlpatterns = [
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("visit/<int:pk>/", views.visit_invoices, name="visit_invoices"),
    path("invoice/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoice/<int:pk>/print/", views.invoice_print, name="invoice_print"),
    path("payment/<int:pk>/receipt/", views.receipt, name="payment_receipt"),
    path("invoice/<int:pk>/add-line/", views.add_line, name="invoice_add_line"),
    path("invoice/<int:pk>/remove-line/", views.remove_line, name="invoice_remove_line"),
    path("invoice/<int:pk>/finalise/", views.finalise, name="invoice_finalise"),
    path("invoice/<int:pk>/pay/", views.pay, name="invoice_pay"),
    path("invoice/<int:pk>/void/", views.void, name="invoice_void"),
    path("invoice/<int:pk>/refund/", views.refund, name="invoice_refund"),
    path("invoice/<int:pk>/claim/", views.create_claim, name="invoice_claim"),
    path("alert/<int:pk>/resolve/", views.alert_resolve, name="alert_resolve"),
    path("prices/", views.price_list, name="price_list"),
    path("cash-up/", views.cash_up, name="cash_up"),
    path("day/", views.day_report, name="day_report"),
    path("claims/", views.claim_list, name="claim_list"),
    path("claims/export/", views.claim_export, name="claim_export"),
    path("claims/<int:pk>/", views.claim_detail, name="claim_detail"),
    path("claims/<int:pk>/<str:action>/", views.claim_action, name="claim_action"),
    path("mpesa/confirm/", views.mpesa_confirmation, name="mpesa_confirm"),
]
