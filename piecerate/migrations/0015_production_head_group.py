from django.db import migrations

GROUP_NAME = "Production Head"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    """Creates the "Production Head" role group — see
    attendance/middleware.py (RoleRestrictionMiddleware, PRODUCTION_HEAD_GROUP)
    for what membership restricts (confined to the Piece Rate module and
    nothing else) and piecerate/permissions.py for what it grants within
    that module (full read/write, same as "Piece Rate Editor"). Add a
    user to this group (Admin > Roles, Django admin, or
    `user.groups.add(Group.objects.get(name="Production Head"))`) to
    give them the entire Piece Rate module and nothing outside it."""

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("piecerate", "0014_alter_ratecardoperation_order_quantity_and_more"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
