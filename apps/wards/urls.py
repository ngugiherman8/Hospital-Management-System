from django.urls import path

from . import views as v

app_name = "wards"
urlpatterns = [
    path("", v.WardBoardView.as_view(), name="board"),
    path("beds/<int:bed_pk>/ready/", v.BedReadyView.as_view(), name="bed_ready"),
    path("admit/visit/<int:visit_pk>/", v.AdmitView.as_view(), name="admit_visit"),
    path("admit/patient/<int:patient_pk>/", v.AdmitView.as_view(), name="admit_patient"),
    path("admissions/<int:pk>/", v.AdmissionView.as_view(), name="admission"),
    path("admissions/<int:pk>/summary/", v.SummaryView.as_view(), name="summary"),
    path("visits/<int:pk>/transfer/", v.TransferView.as_view(), name="transfer"),
    path("visits/<int:pk>/discharge/", v.DischargeView.as_view(), name="discharge"),
]
