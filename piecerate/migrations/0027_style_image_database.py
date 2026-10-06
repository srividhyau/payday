import mimetypes
import warnings

from django.core.files.base import ContentFile
from django.db import migrations, models


def copy_style_images_to_database(apps, schema_editor):
    Style = apps.get_model("piecerate", "Style")
    database = schema_editor.connection.alias
    for style in Style.objects.using(database).filter(image__isnull=False).exclude(image="").iterator():
        try:
            with style.image.open("rb") as source:
                image_data = source.read()
        except (OSError, ValueError) as exc:
            warnings.warn(
                f"Could not read image for style {style.pk} ({style.name}): {exc}",
                RuntimeWarning,
            )
            continue

        style.image_data = image_data
        style.image_content_type = mimetypes.guess_type(style.image.name)[0] or "application/octet-stream"
        style.save(using=database, update_fields=["image_data", "image_content_type"])


def restore_style_images_to_files(apps, schema_editor):
    Style = apps.get_model("piecerate", "Style")
    database = schema_editor.connection.alias
    for style in Style.objects.using(database).exclude(image_data__isnull=True).iterator():
        extension = mimetypes.guess_extension(style.image_content_type) or ".img"
        style.image.save(
            f"style_{style.pk}{extension}",
            ContentFile(bytes(style.image_data)),
            save=False,
        )
        style.save(using=database, update_fields=["image"])


class Migration(migrations.Migration):

    dependencies = [
        ("piecerate", "0026_operatorlink_show_rate_to_operator_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="style",
            name="image_data",
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="style",
            name="image_content_type",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RunPython(copy_style_images_to_database, restore_style_images_to_files),
        migrations.RemoveField(
            model_name="style",
            name="image",
        ),
    ]
