from django.conf import settings


def hospital(request):
    return {"HOSPITAL_NAME": settings.HOSPITAL_NAME}
