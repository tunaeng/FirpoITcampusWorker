from django.db import migrations
from django.contrib.auth.hashers import make_password
import os

def create_superuser(apps, schema_editor):
    User = apps.get_model('auth', 'User') 

    username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
    email = os.getenv('DJANGO_SUPERUSER_EMAIL', 'admin@example.com')
    password = os.getenv('DJANGO_SUPERUSER_PASSWORD', 'admin')

    if not User.objects.filter(username=username).exists():
        User.objects.create(
            username=username,
            email=email,
            password=make_password(password),
            is_superuser=True,
            is_staff=True,
        )


class Migration(migrations.Migration):
    dependencies = []
    operations = [
        migrations.RunPython(create_superuser),
    ]