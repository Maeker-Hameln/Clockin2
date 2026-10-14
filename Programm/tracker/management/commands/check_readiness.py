from django.core.management.base import BaseCommand,CommandError
from django.conf import settings
from tracker.models import Policy
class Command(BaseCommand):
    help='Fachliche und technische Freigabevoraussetzungen prüfen (keine automatische Freigabe).'
    def handle(self,*args,**options):
        p=Policy.objects.filter(pk=1).first();missing=[]
        if settings.DEBUG:missing.append('DEBUG muss im Produktivbetrieb ausgeschaltet sein.')
        if 'HIER_' in settings.SECRET_KEY or len(settings.SECRET_KEY)<40:missing.append('Starker geheimer Django-Schlüssel fehlt.')
        if not p or not p.confirmed or not p.state or not p.rules:missing.append('Betriebliche Anrechnungsregeln und Bundesland müssen bestätigt werden.')
        if not p or not p.retention.strip():missing.append('Aufbewahrungs- und Löschfristen müssen mit Maeker vereinbart werden.')
        if not p or not p.recipient or not p.report_from:missing.append('Berichtsempfänger und erster Berichtsmonat fehlen.')
        if not settings.DEFAULT_FROM_EMAIL:missing.append('Mail-Absender fehlt.')
        if settings.EMAIL_BACKEND=='django.core.mail.backends.smtp.EmailBackend' and not settings.EMAIL_HOST:missing.append('Freigegebener SMTP-Host fehlt.')
        if settings.EMAIL_BACKEND!='django.core.mail.backends.smtp.EmailBackend':missing.append('Produktiven Mailversand bestätigen; Entwicklungsbackend ist nicht freigegeben.')
        if missing:raise CommandError('\n'.join(missing))
        self.stdout.write('Konfiguration vollständig. HTTPS, echte Mailzustellung, Sicherung/Wiederherstellung und fachliche Freigabe separat bestätigen.')
