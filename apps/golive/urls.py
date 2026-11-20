from django.urls import path

from . import views

urlpatterns = [
    path("issues/", views.issue_list, name="issue_list"),
    path("issues/new/", views.issue_create, name="issue_create"),
    path("issues/<int:pk>/", views.issue_update, name="issue_update"),
    path("pilot/", views.pilot_days, name="pilot_days"),
    path("go-no-go/", views.go_no_go, name="go_no_go"),
    path("training/", views.training, name="training"),
    path("training/sheet/", views.training_sheet, name="training_sheet"),
    path("setup/", views.setup_check, name="setup_check"),
    path("audit-review/", views.audit_review, name="audit_review"),
    path("weekly/", views.weekly_review, name="weekly_review"),
    path("forms/", views.forms_index, name="forms_index"),
    path("forms/<slug:slug>/", views.form_print, name="form_print"),
]
