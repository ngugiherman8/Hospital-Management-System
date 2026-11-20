from django import forms

from apps.accounts.models import Role, User

from .models import Appointment


class AppointmentForm(forms.ModelForm):
    scheduled_for = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
        label="Date and time",
    )

    class Meta:
        model = Appointment
        fields = ["clinic", "doctor", "kind", "scheduled_for", "reason"]
        labels = {"kind": "Type", "reason": "Reason for visit"}

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient = patient
        self.fields["doctor"].queryset = User.objects.filter(role=Role.DOCTOR, is_active=True)
        self.fields["doctor"].empty_label = "Any available"

    def clean(self):
        data = super().clean()
        if data.get("kind") == Appointment.Kind.WALK_IN:
            data["scheduled_for"] = None
        return data

    def _post_clean(self):
        self.instance.patient = self.patient
        super()._post_clean()
