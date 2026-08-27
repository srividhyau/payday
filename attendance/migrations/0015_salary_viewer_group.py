from django.db import migrations

GROUP_NAME = "Salary Viewer"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    """Creates the "Salary Viewer" role group — see
    attendance/middleware.py (SalaryViewerRestrictionMiddleware) for what
    membership actually restricts. Add a user to this group (Django admin
    or `user.groups.add(Group.objects.get(name="Salary Viewer"))`) to
    give them view-only access to Salary + Cash Withdrawal and nothing
    else."""

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("attendance", "0014_alter_monthlock_view"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
