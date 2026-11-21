from django import forms

from apps.accounts.models import User

from .models import Contract, Department, LeaveType, Licence, PublicHoliday, StaffProfile


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "head"]


class StaffForm(forms.ModelForm):
    class Meta:
        model = StaffProfile
        fields = [
            "first_name", "last_name", "department", "job_title", "cadre", "date_joined", "date_left", "user",
            "national_id", "kra_pin", "nssf_number", "shif_number", "phone", "email",
            "next_of_kin", "next_of_kin_phone", "bank_name", "bank_account", "notes",
        ]  # fmt: skip
        widgets = {"date_joined": DateInput(), "date_left": DateInput(), "notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        taken = StaffProfile.objects.exclude(pk=self.instance.pk).exclude(user=None).values_list("user_id", flat=True)
        self.fields["user"].queryset = User.objects.filter(is_active=True).exclude(pk__in=list(taken))
        self.fields["user"].help_text = "Only for people who sign in to this system."
        self.fields["department"].queryset = Department.objects.filter(is_active=True)


class ContractForm(forms.ModelForm):
    class Meta:
        model = Contract
        fields = ["kind", "start_date", "end_date", "basic_salary", "hours_per_week", "notes"]
        widgets = {"start_date": DateInput(), "end_date": DateInput()}


class LicenceForm(forms.ModelForm):
    class Meta:
        model = Licence
        fields = ["body", "number", "issued_on", "expires_on", "notes"]
        widgets = {"issued_on": DateInput(), "expires_on": DateInput()}


class HolidayForm(forms.ModelForm):
    class Meta:
        model = PublicHoliday
        fields = ["date", "name"]
        widgets = {"date": DateInput()}


class LeaveTypeForm(forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ["name", "annual_days", "uses_balance", "is_paid"]


class LeaveRequestForm(forms.Form):
    leave_type = forms.ModelChoiceField(LeaveType.objects.none())
    start_date = forms.DateField(widget=DateInput())
    end_date = forms.DateField(widget=DateInput())
    reason = forms.CharField(max_length=300, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["leave_type"].queryset = LeaveType.objects.filter(is_active=True)


class LeaveDecisionForm(forms.Form):
    note = forms.CharField(max_length=300, required=False)


class AdjustLeaveForm(forms.Form):
    staff = forms.ModelChoiceField(StaffProfile.objects.none())
    leave_type = forms.ModelChoiceField(LeaveType.objects.filter(uses_balance=True))
    days = forms.DecimalField(max_digits=6, decimal_places=2, help_text="Use a minus sign to take days away.")
    note = forms.CharField(max_length=300)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["staff"].queryset = StaffProfile.objects.filter(date_left__isnull=True)
