from django.urls import path

from . import views as v

app_name = "rx"
urlpatterns = [
    path("", v.PrescriptionListView.as_view(), name="list"),
    path("visits/<int:pk>/print/", v.PrescriptionPrintView.as_view(), name="print"),
    path("visits/<int:pk>/add/", v.PrescriptionAddView.as_view(), name="add"),
    path("visits/<int:pk>/<int:rx_pk>/cancel/", v.PrescriptionCancelView.as_view(), name="cancel"),
]
