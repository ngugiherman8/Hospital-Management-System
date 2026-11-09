from django.urls import path

from . import views as v

app_name = "billing"
urlpatterns = [
    path("", v.InvoiceListView.as_view(), name="list"),
    path("visits/<int:visit_pk>/", v.VisitInvoiceView.as_view(), name="visit"),
    path("invoices/<int:pk>/", v.InvoiceDetailView.as_view(), name="detail"),
    path("invoices/<int:pk>/print/", v.InvoicePrintView.as_view(), name="print"),
    path("invoices/<int:pk>/add-line/", v.AddLineView.as_view(), name="add_line"),
    path("invoices/<int:pk>/lines/<int:line_pk>/remove/", v.RemoveLineView.as_view(), name="remove_line"),
    path("invoices/<int:pk>/finalise/", v.FinaliseView.as_view(), name="finalise"),
    path("invoices/<int:pk>/pay/", v.PayView.as_view(), name="pay"),
    path("invoices/<int:pk>/mpesa-prompt/", v.MpesaPromptView.as_view(), name="mpesa_prompt"),
    path("invoices/<int:pk>/refund/", v.RefundView.as_view(), name="refund"),
    path("invoices/<int:pk>/void/", v.VoidView.as_view(), name="void"),
    path("invoices/<int:pk>/credit/", v.CreditNoteView.as_view(), name="credit"),
    path("receipts/<int:pay_pk>/", v.ReceiptView.as_view(), name="receipt"),
    path("mpesa/callback/<str:token>/", v.mpesa_callback, name="mpesa_callback"),
]
