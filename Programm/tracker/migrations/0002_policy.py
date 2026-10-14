from django.db import migrations

def initial(apps,schema_editor):
    apps.get_model('tracker','Policy').objects.get_or_create(pk=1,defaults={"state":"NI"})
class Migration(migrations.Migration):
    dependencies=[('tracker','0001_initial')]
    operations=[migrations.RunPython(initial,migrations.RunPython.noop)]
