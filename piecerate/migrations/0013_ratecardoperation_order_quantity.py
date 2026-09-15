from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("piecerate", "0012_piece_rate_editor_group"),
    ]

    operations = [
        migrations.AddField(
            model_name="ratecardoperation",
            name="order_quantity",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
