from django.urls import path

from . import views

urlpatterns = [
    path("triage/<int:pk>/", views.triage, name="triage"),
    path("visit/<int:pk>/", views.visit_detail, name="visit_detail"),
    path("visit/<int:pk>/note/", views.note_save, name="note_save"),
    path("visit/<int:pk>/diagnosis/", views.diagnosis_add, name="diagnosis_add"),
    path("visit/<int:pk>/outcome/", views.outcome_save, name="outcome_save"),
    path("icd10/", views.icd10_lookup, name="icd10_lookup"),
]
