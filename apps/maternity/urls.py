from django.urls import path

from . import views

urlpatterns = [
    path("", views.pregnancy_list, name="pregnancy_list"),
    path("due/", views.due_list, name="maternity_due"),
    path("patient/<int:patient_pk>/", views.for_patient, name="maternity_for_patient"),
    path("patient/<int:patient_pk>/start/", views.pregnancy_start, name="pregnancy_start"),
    path("episode/<int:pk>/", views.pregnancy_detail, name="pregnancy_detail"),
    path("episode/<int:pk>/contact/", views.contact_add, name="antenatal_contact_add"),
    path("episode/<int:pk>/update/", views.edd_update, name="pregnancy_update"),
    path("episode/<int:pk>/delivery/", views.delivery_add, name="delivery_add"),
    path("delivery/<int:pk>/postnatal/<str:who>/", views.postnatal_add, name="postnatal_add"),
]
