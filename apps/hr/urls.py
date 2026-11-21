from django.urls import path

from . import views

urlpatterns = [
    path("", views.staff_list, name="staff_list"),
    path("new/", views.staff_create, name="staff_create"),
    path("<int:pk>/", views.staff_detail, name="staff_detail"),
    path("<int:pk>/edit/", views.staff_edit, name="staff_edit"),
    path("<int:pk>/contracts/new/", views.contract_add, name="contract_add"),
    path("contracts/<int:pk>/end/", views.contract_end, name="contract_end"),
    path("<int:pk>/licences/new/", views.licence_add, name="licence_add"),
    path("licences/", views.licence_alerts, name="licence_alerts"),
    path("departments/", views.department_list, name="department_list"),
    path("leave/", views.my_leave, name="my_leave"),
    path("leave/<int:pk>/cancel/", views.leave_cancel, name="leave_cancel"),
    path("leave/approvals/", views.leave_approvals, name="leave_approvals"),
    path("leave/<int:pk>/<str:action>/", views.leave_decide, name="leave_decide"),
    path("leave/balances/", views.leave_balances, name="leave_balances"),
    path("leave/accrue/", views.leave_accrue, name="leave_accrue"),
    path("leave/types/", views.leave_types, name="leave_types"),
    path("holidays/", views.holidays, name="holidays"),
]
