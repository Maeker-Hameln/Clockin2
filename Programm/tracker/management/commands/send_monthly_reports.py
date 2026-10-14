from django.core.management.base import BaseCommand,CommandError
from django.core.exceptions import ValidationError
from tracker.reports import run_due
class Command(BaseCommand):
    help='Fällige Monatsberichte nachholen; unklare Zustellungen bleiben gesperrt.'
    def handle(self,*args,**options):
        try:
            results=run_due()
        except ValidationError as exc: raise CommandError('; '.join(exc.messages))
        for pk,status in results: self.stdout.write(f'Bericht {pk}: {status}')
        if any(s in ['failed','unknown','sending'] for _,s in results): raise CommandError('Versandfehler oder unklare Zustellung: Verwaltung prüfen.')
