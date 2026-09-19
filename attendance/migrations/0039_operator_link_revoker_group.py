from django.db import migrations

GROUP_NAME = "Operator Link Revoker"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    """Creates the "Operator Link Revoker" role group — see
    attendance/middleware.py (RoleRestrictionMiddleware, _ROLE_ACCESS) for
    what membership actually grants: the Operator Links page, where they
    can revoke an operator's link (but not generate, regenerate, or see
    the link/QR code itself), and nothing else."""

    dependencies = [
        ("attendance", "0038_remove_employee_profession_tax_and_more"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
