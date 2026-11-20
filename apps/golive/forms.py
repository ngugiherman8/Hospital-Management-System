from django import forms

from .models import GoNoGo, Issue, PilotDay, Training


class IssueForm(forms.ModelForm):
    class Meta:
        model = Issue
        fields = ["title", "description", "severity", "department", "workaround"]
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class IssueUpdateForm(forms.ModelForm):
    class Meta:
        model = Issue
        fields = ["status", "workaround"]

    def clean(self):
        data = super().clean()
        if data.get("status") == Issue.Status.WORKAROUND and not (data.get("workaround") or self.instance.workaround):
            self.add_error("workaround", "Say what staff should do meanwhile.")
        return data


class PilotDayForm(forms.ModelForm):
    class Meta:
        model = PilotDay
        fields = ["day", "department", "paper_cash", "paper_visits", "paper_dispensed", "explanation"]
        widgets = {"day": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}


class GoNoGoForm(forms.ModelForm):
    class Meta:
        model = GoNoGo
        fields = ["department", "item", "agreed_by", "agreed_on", "note"]
        widgets = {"agreed_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"), "item": forms.HiddenInput()}


class TrainingForm(forms.ModelForm):
    class Meta:
        model = Training
        fields = ["user", "session_date", "topic", "completed_without_help", "trainer", "is_super_user"]
        widgets = {"session_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
