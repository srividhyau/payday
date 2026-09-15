from django.db import migrations

GROUP_NAME = "Piece Rate Editor"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP_NAME)


def delete_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    """Creates the "Piece Rate Editor" role group — see
    piecerate/permissions.py for what membership grants. Add a user to
    this group (Admin > Roles, Django admin, or
    `user.groups.add(Group.objects.get(name="Piece Rate Editor"))`) to
    let them add/edit/delete/reorder Master Operations and Templates.
    Everyone with general app access can still view both pages either
    way; this only gates the write actions."""

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("piecerate", "0011_remove_style_source"),
    ]

    operations = [
        migrations.RunPython(create_group, delete_group),
    ]
