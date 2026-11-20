import datetime
from collections import Counter

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import FailedLogin, Role, User
from apps.appointments.models import Appointment
from apps.billing.services import day_reconciliation
from apps.core.models import AuditLog
from apps.core.permissions import role_required
from apps.stock.services import stock_levels

from . import readiness
from .downtime import FORMS, HEADER
from .forms import GoNoGoForm, IssueForm, IssueUpdateForm, PilotDayForm, TrainingForm
from .models import DEPARTMENTS, GoNoGo, Issue, PilotDay, Training

EVERYONE = tuple(r.value for r in Role)
LEADS = (Role.MANAGER,)  # administrators always pass


# ------------------------------------------------------------------ issues


@role_required(*EVERYONE)
def issue_list(request):
    show = request.GET.get("show", "open")
    issues = Issue.objects.select_related("reported_by")
    if show == "open":
        issues = issues.filter(status__in=[Issue.Status.OPEN, Issue.Status.WORKAROUND])
    order = {s: i for i, s in enumerate(["critical", "high", "medium", "low"])}
    issues = sorted(issues, key=lambda i: (order[i.severity], i.id))
    counts = Counter(i.severity for i in Issue.objects.filter(status__in=[Issue.Status.OPEN, Issue.Status.WORKAROUND]))
    return render(
        request,
        "golive/issue_list.html",
        {"issues": issues, "show": show, "counts": counts, "can_edit": request.user.has_role(*LEADS)},
    )


@role_required(*EVERYONE)
def issue_create(request):
    form = IssueForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        issue = form.save(commit=False)
        issue.reported_by = request.user
        issue.save()
        messages.success(request, "Problem logged.")
        return redirect("issue_list")
    return render(request, "golive/issue_form.html", {"form": form, "title": "Report a problem"})


@role_required(*LEADS)
def issue_update(request, pk):
    issue = get_object_or_404(Issue, pk=pk)
    form = IssueUpdateForm(request.POST or None, instance=issue)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Problem updated.")
        return redirect("issue_list")
    return render(request, "golive/issue_form.html", {"form": form, "title": f"Update: {issue.title}", "issue": issue})


# ------------------------------------------------------------------ pilot days


@role_required(*LEADS)
def pilot_days(request):
    instance = None
    if request.method == "POST":
        # Saving the same day and department again corrects the earlier entry.
        instance = PilotDay.objects.filter(
            day=request.POST.get("day") or None, department=request.POST.get("department")
        ).first()
    form = PilotDayForm(request.POST or None, instance=instance, initial={"day": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        day = form.save(commit=False)
        day.entered_by = request.user
        day.save()
        messages.success(request, "Saved. The comparison is below.")
        return redirect("pilot_days")
    days = list(PilotDay.objects.all()[:30])
    for d in days:
        d.rows = d.comparison()
    return render(request, "golive/pilot_days.html", {"form": form, "days": days})


# ------------------------------------------------------------------ go or no-go


def _unresolved_critical():
    return Issue.objects.filter(
        severity=Issue.Severity.CRITICAL, status__in=[Issue.Status.OPEN, Issue.Status.WORKAROUND]
    )


@role_required(*LEADS)
def go_no_go(request):
    if request.method == "POST":
        if not request.user.has_role():  # administrators only
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if "withdraw" in request.POST:
            GoNoGo.objects.filter(pk=request.POST["withdraw"]).first().delete()
            messages.success(request, "Agreement withdrawn.")
            return redirect("go_no_go")
        form = GoNoGoForm(request.POST)
        if form.is_valid():
            item = form.cleaned_data["item"]
            if item == "no_critical" and _unresolved_critical().exists():
                messages.error(request, "A critical problem is still open, so this cannot be agreed.")
            else:
                obj, _ = GoNoGo.objects.update_or_create(
                    department=form.cleaned_data["department"],
                    item=item,
                    defaults={
                        "agreed_by": form.cleaned_data["agreed_by"],
                        "agreed_on": form.cleaned_data["agreed_on"],
                        "note": form.cleaned_data["note"],
                        "recorded_by": request.user,
                    },
                )
                messages.success(request, "Recorded.")
        else:
            messages.error(request, "Give the name of the person who agreed.")
        return redirect("go_no_go")
    agreed = {(g.department, g.item): g for g in GoNoGo.objects.all()}
    board = []
    for code, name in DEPARTMENTS:
        items = [(key, text, agreed.get((code, key))) for key, text in GoNoGo.ITEMS]
        board.append({"code": code, "name": name, "items": items, "go": all(i[2] for i in items)})
    return render(
        request,
        "golive/go_no_go.html",
        {
            "board": board,
            "critical_open": _unresolved_critical().count(),
            "today": timezone.localdate(),
            "can_record": request.user.has_role(),
        },
    )


# ------------------------------------------------------------------ training


@role_required(*LEADS)
def training(request):
    form = TrainingForm(request.POST or None, initial={"session_date": timezone.localdate()})
    if request.method == "POST":
        if not request.user.has_role():
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "Attendance recorded.")
            return redirect("training")
    trained = set(Training.objects.filter(completed_without_help=True).values_list("user_id", flat=True))
    untrained = User.objects.filter(is_active=True).exclude(pk__in=trained).exclude(username__startswith="train-")
    return render(
        request,
        "golive/training.html",
        {
            "form": form,
            "sessions": Training.objects.select_related("user")[:100],
            "untrained": untrained,
            "can_record": request.user.has_role(),
        },
    )


@role_required(*LEADS)
def training_sheet(request):
    """A blank sign-in sheet to print and pass round the room."""
    return render(request, "golive/training_sheet.html", {"blank_rows": range(20)})


# ------------------------------------------------------------------ set-up check


@role_required(*LEADS)
def setup_check(request):
    results = readiness.run()
    return render(request, "golive/setup_check.html", {"results": results, "ready": readiness.ready(results)})


# ------------------------------------------------------------------ audit review


@role_required(*LEADS)
def audit_review(request):
    try:
        days = max(1, min(int(request.GET.get("days", 7)), 90))
    except ValueError:
        days = 7
    try:
        threshold = max(5, int(request.GET.get("threshold", 40)))
    except ValueError:
        threshold = 40
    since = timezone.now() - datetime.timedelta(days=days)
    logs = AuditLog.objects.filter(timestamp__gte=since)

    denied = logs.filter(action="denied").values("user__username").annotate(n=Count("id")).order_by("-n")[:15]
    deleted = (
        logs.filter(action="delete").values("user__username", "model_label").annotate(n=Count("id")).order_by("-n")[:15]
    )
    views = logs.filter(action="view", model_label="patients.Patient")
    odd_hours = [
        v
        for v in views.select_related("user").order_by("-timestamp")[:5000]
        if timezone.localtime(v.timestamp).hour < 6 or timezone.localtime(v.timestamp).hour >= 22
    ][:50]
    per_day = {}
    for username, ts, oid in views.values_list("user__username", "timestamp", "object_id"):
        per_day.setdefault((username, timezone.localtime(ts).date()), set()).add(oid)
    heavy = sorted(((u, d, len(ids)) for (u, d), ids in per_day.items() if len(ids) >= threshold), key=lambda r: -r[2])[
        :30
    ]
    failed = (
        FailedLogin.objects.filter(at__gte=since)
        .values("username")
        .annotate(n=Count("id"))
        .filter(n__gte=3)
        .order_by("-n")[:15]
    )
    return render(
        request,
        "golive/audit_review.html",
        {
            "days": days,
            "threshold": threshold,
            "denied": denied,
            "deleted": deleted,
            "odd_hours": odd_hours,
            "heavy": heavy,
            "failed": failed,
        },
    )


# ------------------------------------------------------------------ weekly review


@role_required(*LEADS)
def weekly_review(request):
    today = timezone.localdate()
    week = []
    for back in range(6, -1, -1):
        day = today - datetime.timedelta(days=back)
        money = day_reconciliation(day)
        week.append(
            {
                "day": day,
                "visits": Appointment.objects.filter(visit_date=day).count(),
                "billed": money["billed"],
                "net": money["net"],
                "ledger_ok": money["ledger_ok"] and not money["unclosed_cashiers"] and not money["unexplained"],
            }
        )
    open_issues = Issue.objects.filter(status__in=[Issue.Status.OPEN, Issue.Status.WORKAROUND])
    return render(
        request,
        "golive/weekly_review.html",
        {
            "week": week,
            "issues": sorted(open_issues, key=lambda i: ["critical", "high", "medium", "low"].index(i.severity)),
            "overdue": [i for i in open_issues if i.overdue],
            "low_stock": [r for r in stock_levels() if r["low"]],
            "unmatched_pilot_days": [d for d in PilotDay.objects.all()[:30] if d.needs_explaining],
        },
    )


# ------------------------------------------------------------------ downtime forms


@role_required(*EVERYONE)
def forms_index(request):
    return render(request, "golive/forms_index.html", {"forms": FORMS})


@role_required(*EVERYONE)
def form_print(request, slug):
    form = FORMS.get(slug)
    if form is None:
        from django.http import Http404

        raise Http404
    return render(
        request, "golive/form_print.html", {"form": form, "header": HEADER, "slug": slug, "blank_rows": range(14)}
    )
