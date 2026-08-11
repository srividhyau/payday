from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import UploadForm
from .importer import import_file
from .models import UploadBatch


def upload_view(request):
    if request.method == "POST":
        form = UploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES["file"]
            try:
                batch = import_file(uploaded, file_name=uploaded.name)
                messages.success(
                    request,
                    f"Imported {batch.row_count} rows from {batch.period_start} to "
                    f"{batch.period_end}.",
                )
            except Exception as exc:  # noqa: BLE001 - surface any parse/import error to HR
                messages.error(request, f"Import failed: {exc}")
            return redirect("upload")
    else:
        form = UploadForm()

    recent_batches = UploadBatch.objects.all()[:10]
    return render(request, "attendance/upload.html", {"form": form, "batches": recent_batches})
