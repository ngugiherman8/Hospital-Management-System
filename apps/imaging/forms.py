import os

from django import forms

from .models import ImagingItem

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".pdf")
YES_NO = [("", "Not answered"), ("yes", "Yes"), ("no", "No")]


def _to_bool(value):
    return {"yes": True, "no": False}.get(value)


class ImagingOrderForm(forms.Form):
    studies = forms.ModelMultipleChoiceField(queryset=ImagingItem.objects.none(), label="Scans")
    clinical_reason = forms.CharField(max_length=300, label="Clinical reason")
    may_be_pregnant = forms.ChoiceField(choices=YES_NO, required=False, label="Could the patient be pregnant?")
    contrast_allergy = forms.ChoiceField(choices=YES_NO, required=False, label="Allergy to contrast?")
    kidney_problem = forms.ChoiceField(choices=YES_NO, required=False, label="Kidney problems?")

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.patient = patient
        self.fields["studies"].queryset = ImagingItem.objects.filter(
            test__is_active=True, modality__is_active=True
        ).select_related("test", "modality")
        self.fields["studies"].label_from_instance = lambda i: f"{i.test.name} ({i.modality.name})"

    def clean(self):
        data = super().clean()
        studies = data.get("studies") or []
        if any(s.uses_radiation for s in studies) and self.patient and self.patient.sex == "F":
            if 10 <= self.patient.age <= 55 and not data.get("may_be_pregnant"):
                self.add_error("may_be_pregnant", "Answer this before ordering a study that uses radiation.")
        if any(s.uses_contrast for s in studies):
            for name in ("contrast_allergy", "kidney_problem"):
                if not data.get(name):
                    self.add_error(name, "Answer this before ordering a contrast study.")
        return data

    def answers(self):
        data = self.cleaned_data
        return {
            "may_be_pregnant": _to_bool(data.get("may_be_pregnant")),
            "contrast_allergy": _to_bool(data.get("contrast_allergy")),
            "kidney_problem": _to_bool(data.get("kidney_problem")),
        }


class ReportForm(forms.Form):
    findings = forms.CharField(widget=forms.Textarea(attrs={"rows": 5}))
    impression = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    critical = forms.BooleanField(required=False, label="Critical finding: the ordering clinician must be alerted")
    sign_final = forms.BooleanField(required=False, label="Sign as final (locks the report)")
    reason = forms.CharField(
        max_length=300, required=False, label="Reason for addendum (needed once a report is final)"
    )


class StudyFileForm(forms.Form):
    file = forms.FileField(label="JPEG or PDF")
    note = forms.CharField(max_length=200, required=False)

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if os.path.splitext(upload.name)[1].lower() not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Only JPEG or PDF files can be attached.")
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError("The file is larger than 10 MB.")
        return upload
