# Generated by Django 2.2.12 on 2021-03-31 08:44

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maasserver", "0233_drop_switch"),
    ]

    operations = [
        migrations.AddField(
            model_name="node",
            name="register_vmhost",
            field=models.BooleanField(default=False),
        ),
    ]