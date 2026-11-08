from django.urls import path

from . import views as v

app_name = "orders"
urlpatterns = [
    path("lab/", v.LabWorklistView.as_view(), name="lab"),
    path("lab/<int:order_pk>/collect/", v.LabCollectView.as_view(), name="collect"),
    path("lab/<int:order_pk>/result/", v.LabResultView.as_view(), name="result"),
    path("lab/<int:order_pk>/correct/", v.LabCorrectView.as_view(), name="correct"),
    path("visits/<int:pk>/add/", v.OrderAddView.as_view(), name="add"),
    path("visits/<int:pk>/<int:order_pk>/cancel/", v.OrderCancelView.as_view(), name="cancel"),
]
