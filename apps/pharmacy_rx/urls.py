from django.urls import path

from . import views

urlpatterns = [path("visit/<int:pk>/prescribe/", views.prescribe, name="prescribe")]
