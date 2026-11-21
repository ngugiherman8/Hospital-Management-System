from django.urls import path

from . import bulk_views, views

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
    path("bulk/", bulk_views.bulk_index, name="bulk_index"),
    path("bulk/templates.zip", bulk_views.bulk_starter_zip, name="bulk_zip"),
    path("bulk/<slug:kind>/starter.csv", bulk_views.bulk_starter, name="bulk_starter"),
    path("bulk/problems.csv", bulk_views.bulk_errors, name="bulk_errors"),
    path("bulk/<slug:kind>/", bulk_views.bulk_kind, name="bulk_kind"),
    path("bulk/<slug:kind>/confirm/", bulk_views.bulk_confirm, name="bulk_confirm"),
    path("bulk/<slug:kind>/template.csv", bulk_views.bulk_template, name="bulk_template"),
    path("forms/", views.forms_index, name="forms_index"),
    path("forms/<slug:slug>/", views.form_print, name="form_print"),
]
