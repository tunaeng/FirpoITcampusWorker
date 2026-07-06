from django.db import migrations


def create_default_schedule(apps, schema_editor):
    Schedule = apps.get_model("scheduler", "Schedule")
    if not Schedule.objects.filter(name="Сбор упражнений").exists():
        Schedule.objects.create(
            name="Сбор упражнений",
            interval_seconds=3600,
            is_active=True,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_default_schedule, reverse_code=migrations.RunPython.noop),
    ]
