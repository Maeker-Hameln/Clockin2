from django.db import migrations

def triggers(apps,schema_editor):
    definitions={
        'segment':"NEW.active=1 AND EXISTS (SELECT 1 FROM tracker_segment old WHERE old.id<>NEW.id AND old.active=1 AND old.student_id=NEW.student_id AND old.start<=NEW.end AND old.end>=NEW.start)",
        'interval':"EXISTS (SELECT 1 FROM tracker_interval old WHERE old.id<>NEW.id AND old.student_id=NEW.student_id AND (NEW.end IS NULL OR old.start<NEW.end) AND (old.end IS NULL OR NEW.start<old.end))",
        'pause':"(NEW.end IS NULL OR NEW.end>NEW.start) AND EXISTS (SELECT 1 FROM tracker_pause old WHERE old.id<>NEW.id AND old.interval_id=NEW.interval_id AND (old.end IS NULL OR old.end>old.start) AND (NEW.end IS NULL OR old.start<NEW.end) AND (old.end IS NULL OR NEW.start<old.end))",
    }
    for name,condition in definitions.items():
        # SQLite assigns NEW.id=-1 before an insert; update excludes its own existing row.
        for event in ['INSERT','UPDATE']:
            schema_editor.execute(f"CREATE TRIGGER portable_{name}_{event.lower()} BEFORE {event} ON tracker_{name} WHEN {condition} BEGIN SELECT RAISE(ABORT,'Unzulaessige Ueberschneidung'); END")
    apps.get_model('tracker','Policy').objects.get_or_create(pk=1,defaults={'state':'NI'})

def reverse(apps,schema_editor):
    for name in ['segment','interval','pause']:
        for event in ['insert','update']:schema_editor.execute(f'DROP TRIGGER IF EXISTS portable_{name}_{event}')
class Migration(migrations.Migration):
    dependencies=[('tracker','0001_initial')]
    operations=[migrations.RunPython(triggers,reverse)]
