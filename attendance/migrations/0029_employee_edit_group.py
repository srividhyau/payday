from django.db import migrations

GROUP_NAME = "Employee Edit"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    """Creates the "Employee Edit" role group — see
    attendance/middleware.py (RoleRestrictionMiddleware, _ROLE_ACCESS) for
    what membership actually grants. Add a user to this group (Django
    admin, the in-app Roles page, or
    `user.groups.add(Group.objects.get(name="Employee Edit"))`) to give
    them access to Employee Details (view/add/edit employees) and nothing
    else."""

    dependencies = [
        ("attendance", "0028_employee_category_subcategory_choices"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
