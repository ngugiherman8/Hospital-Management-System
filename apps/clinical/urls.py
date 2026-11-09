from django.urls import path

from . import views as v

app_name = "clinical"
urlpatterns = [
    path("triage/<int:entry_pk>/", v.TriageView.as_view(), name="triage"),
    path("call-in/<int:entry_pk>/", v.CallInView.as_view(), name="call_in"),
    path("consult/<int:entry_pk>/", v.ConsultView.as_view(), name="consult"),
    path("visits/<int:pk>/", v.VisitDetailView.as_view(), name="visit"),
    path("visits/<int:pk>/icd10/", v.IcdSearchView.as_view(), name="icd_search"),
    path("visits/<int:pk>/diagnoses/add/", v.DiagnosisAddView.as_view(), name="diagnosis_add"),
    path(
        "visits/<int:pk>/diagnoses/<int:diag_pk>/<str:action>/",
        v.DiagnosisActionView.as_view(),
        name="diagnosis_action",
    ),
    path("visits/<int:pk>/notes/add/", v.ProgressNoteView.as_view(), name="note_add"),
    path("visits/<int:pk>/notes/<int:note_pk>/amend/", v.NoteAmendView.as_view(), name="note_amend"),
    path("visits/<int:pk>/vitals/add/", v.VitalsAddView.as_view(), name="vitals_add"),
    path("visits/<int:pk>/send-to-lab/", v.SendToLabView.as_view(), name="send_to_lab"),
    path("visits/<int:pk>/referral/", v.ReferralLetterView.as_view(), name="referral"),
    path("patients/<int:pk>/history/", v.PatientHistoryView.as_view(), name="history"),
    path("patients/<int:pk>/break-glass/", v.BreakGlassView.as_view(), name="break_glass"),
]
