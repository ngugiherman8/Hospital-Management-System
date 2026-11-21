from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import role_required

from . import bulk

SHOWN = 300  # rows listed on screen; the full count is always in the summary


def _importer(kind):
    importer = bulk.BY_KEY.get(kind)
    if importer is None:
        raise Http404
    return importer


def _csv(text, name):
    response = HttpResponse(text, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@role_required()  # administrators only
def bulk_index(request):
    return render(request, "golive/bulk_index.html", {"importers": sorted(bulk.IMPORTERS, key=lambda i: i.order)})


@role_required()
def bulk_kind(request, kind):
    importer = _importer(kind)
    if request.method == "POST":
        upload = request.FILES.get("file")
        if upload is None:
            messages.error(request, "Choose a CSV file first.")
            return redirect("bulk_kind", kind=kind)
        try:
            text = bulk.decode(upload.read(bulk.MAX_BYTES + 1))
            results = bulk.run(importer, text, request.user, dry_run=True)
        except bulk.BulkError as exc:
            messages.error(request, str(exc))
            return redirect("bulk_kind", kind=kind)
        request.session["bulk"] = {"kind": kind, "text": text, "name": upload.name}
        return _show(request, importer, results, preview=True, name=upload.name)
    return render(request, "golive/bulk_kind.html", {"importer": importer})


@role_required()
@require_POST
def bulk_confirm(request, kind):
    importer = _importer(kind)
    pending = request.session.get("bulk")
    if not pending or pending.get("kind") != kind:
        messages.error(request, "That preview has expired. Upload the file again.")
        return redirect("bulk_kind", kind=kind)
    try:
        results = bulk.run(importer, pending["text"], request.user, dry_run=False)
    except bulk.BulkError as exc:
        messages.error(request, str(exc))
        return redirect("bulk_kind", kind=kind)
    request.session.pop("bulk", None)
    request.session["bulk_errors"] = [[r.row, r.label, r.message] for r in results if r.status == "error"][:2000]
    return _show(request, importer, results, preview=False, name=pending.get("name", ""))


def _show(request, importer, results, preview, name):
    counts = bulk.summarise(results)
    ordered = sorted(results, key=lambda r: (r.status != "error", r.row))  # problems first
    return render(
        request,
        "golive/bulk_result.html",
        {
            "importer": importer,
            "preview": preview,
            "name": name,
            "counts": counts,
            "total": len(results),
            "importable": counts["created"] + counts["updated"],
            "rows": ordered[:SHOWN],
            "hidden": max(0, len(ordered) - SHOWN),
        },
    )


@role_required()
def bulk_template(request, kind):
    importer = _importer(kind)
    return _csv(bulk.template_csv(importer), f"{importer.key}-template.csv")


@role_required()
def bulk_errors(request):
    rows = request.session.get("bulk_errors", [])
    results = [bulk.Result(r[0], r[1], "error", r[2]) for r in rows]
    return _csv(bulk.errors_csv(results), "import-problems.csv")


@role_required()
def bulk_starter(request, kind):
    importer = _importer(kind)
    if not importer.starter:
        raise Http404
    return _csv(bulk.template_csv(importer, starter=True), f"{importer.key}-starter-data.csv")


@role_required()
def bulk_starter_zip(request):
    response = HttpResponse(bulk.starter_zip(), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="hms-import-templates.zip"'
    return response
