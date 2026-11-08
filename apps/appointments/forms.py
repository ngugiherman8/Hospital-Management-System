from django import forms
from django.utils import timezone

from apps.core.models import User

from .models import Appointment


class AppointmentForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ["clinic", "doctor", "starts_at", "reason"]
        widgets = {"starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["doctor"].queryset = User.objects.filter(role="doctor", is_active=True)
        self.fields["starts_at"].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean_starts_at(self):
        when = self.cleaned_data["starts_at"]
        if when < timezone.now():
            raise forms.ValidationError("Choose a time in the future.")
        return when

    def clean(self):
        data = super().clean()
        doctor, when = data.get("doctor"), data.get("starts_at")
        if doctor and when:
            clash = Appointment.objects.filter(doctor=doctor, starts_at=when).exclude(
                status__in=["cancelled", "no_show"]
            )
            if clash.exists():
                self.add_error("starts_at", "That doctor is already booked at this time.")
        return data
