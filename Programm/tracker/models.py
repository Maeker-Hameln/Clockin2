import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, F, Func
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, DateTimeRangeField, RangeOperators
USER = settings.AUTH_USER_MODEL
PHASES = [('practice','Praxis'),('university','Hochschule'),('mixed','Gemischt')]

def validate_week(value):
    if not isinstance(value, list) or len(value)!=7:
        raise ValidationError('Sieben Wochentage (Montag bis Sonntag) sind erforderlich.')
    for day in value:
        if not isinstance(day,dict) or set(day)!= {'target','university'}:
            raise ValidationError('Soll und Hochschulanrechnung je Wochentag fehlen.')
        t,c=day['target'],day['university']
        if any(v is not None and (type(v)!=int or not 0<=v<=1440) for v in [t,c]):
            raise ValidationError('Tageswerte müssen gültige Minuten oder unbekannt sein.')
        if t is None or c is None: continue
        if not 0<=c<=t<=1440:
            raise ValidationError('Minuten müssen zwischen 0 und 1440 liegen; Anrechnung darf Soll nicht übersteigen.')

class Policy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True,default=1,editable=False)
    state = models.CharField('Bundesland',max_length=2,blank=True,choices=[(s,s) for s in ['BW','BY','BE','BB','HB','HH','HE','MV','NI','NW','RP','SL','SN','ST','SH','TH']])
    confirmed = models.BooleanField('Anrechnungsregeln betrieblich bestätigt',default=False)
    rules = models.TextField('Bestätigte Regeln (Hochschule laut Tagesplan; Urlaub restliches betriebliches Soll; Feiertag gesamtes Soll)',blank=True)
    recipient = models.EmailField('Berichtsempfänger',blank=True)
    report_from = models.DateField('Erster Berichtsmonat',null=True,blank=True,help_text='Erster Kalendertag; keine erfundene rückwirkende Versandhistorie.')
    retention = models.TextField('Vereinbarte Aufbewahrungs- und Löschfristen',blank=True)
    def clean(self):
        if self.confirmed and (not self.state or not self.rules.strip()):
            raise ValidationError('Bundesland und bestätigte Regeln fehlen.')
        if self.report_from and self.report_from.day!=1:
            raise ValidationError('Der Berichtsstart muss ein Monatserster sein.')
    def __str__(self): return 'Betriebliche Einstellungen'
    class Meta: verbose_name='Betriebliche Einstellungen'; verbose_name_plural=verbose_name

class Plan(models.Model):
    student = models.ForeignKey(USER,verbose_name="Student",on_delete=models.PROTECT)
    created_at = models.DateTimeField("Zeitpunkt",auto_now_add=True)
    document = models.FileField('Dokument',upload_to='plans/%Y/%m/',blank=True)
    recognition = models.TextField("Erkennung",blank=True)
    status = models.CharField('Status',max_length=12,default='draft',choices=[('draft','Entwurf'),('submitted','Zur Prüfung'),('active','Aktiviert'),('replaced','Abgelöst')])
    activated_by = models.ForeignKey(USER,verbose_name="Aktiviert von",on_delete=models.PROTECT,null=True,related_name='+')
    activated_at = models.DateTimeField("Aktiviert am",null=True)
    reason = models.TextField("Begründung",blank=True)
    def __str__(self): return f'Plan #{self.pk} – {self.student}'
    class Meta: verbose_name='Planfassung'; verbose_name_plural='Planfassungen'

class Segment(models.Model):
    plan = models.ForeignKey(Plan,verbose_name="Planfassung",on_delete=models.PROTECT,related_name='segments')
    student = models.ForeignKey(USER,verbose_name="Student",on_delete=models.PROTECT)
    start = models.DateField('Beginn')
    end = models.DateField('Ende')
    phase = models.CharField('Phase',max_length=12,choices=PHASES)
    week = models.JSONField('Tagesminuten',validators=[validate_week])
    uncertainties = models.TextField('Unklarheiten',blank=True)
    active = models.BooleanField("Aktiv",default=False)
    class Meta:
        constraints=[models.CheckConstraint(condition=Q(end__gte=F('start')),name='segment_date_order'),ExclusionConstraint(name='no_active_plan_overlap',expressions=[('student',RangeOperators.EQUAL),(Func(F('start'),F('end'),models.Value('[]'),function='DATERANGE',output_field=DateRangeField()),RangeOperators.OVERLAPS)],condition=Q(active=True))]
    def clean(self):
        validate_week(self.week)
        if self.end < self.start: raise ValidationError('Ende liegt vor Beginn.')
        if self.plan_id and self.student_id != self.plan.student_id: raise ValidationError('Plan und Student passen nicht zusammen.')

class Interval(models.Model):
    student = models.ForeignKey(USER,verbose_name="Student",on_delete=models.PROTECT)
    start = models.DateTimeField('Beginn')
    end = models.DateTimeField('Ende',null=True,blank=True)
    class Meta:
        verbose_name='Arbeitsintervall'; verbose_name_plural='Arbeitsintervalle'
        constraints=[ExclusionConstraint(name='no_work_overlap',expressions=[('student',RangeOperators.EQUAL),(Func(F('start'),F('end'),models.Value('[)'),function='TSTZRANGE',output_field=DateTimeRangeField()),RangeOperators.OVERLAPS)]),models.UniqueConstraint(fields=['student'],condition=Q(end__isnull=True),name='one_open_interval'),models.CheckConstraint(condition=Q(end__isnull=True)|Q(end__gt=F('start')),name='interval_order')]

class Pause(models.Model):
    interval = models.ForeignKey(Interval,verbose_name='Arbeitsintervall',on_delete=models.PROTECT,related_name='pauses')
    start = models.DateTimeField('Beginn')
    end = models.DateTimeField('Ende',null=True)
    class Meta:
        constraints=[ExclusionConstraint(name='no_pause_overlap',expressions=[('interval',RangeOperators.EQUAL),(Func(F('start'),F('end'),models.Value('[)'),function='TSTZRANGE',output_field=DateTimeRangeField()),RangeOperators.OVERLAPS)]),models.UniqueConstraint(fields=['interval'],condition=Q(end__isnull=True),name='one_open_pause'),models.CheckConstraint(condition=Q(end__isnull=True)|Q(end__gte=F('start')),name='pause_order')]

class Stamp(models.Model):
    key = models.UUIDField("Aktionskennung",default=uuid.uuid4)
    student = models.ForeignKey(USER,verbose_name="Student",on_delete=models.PROTECT)
    action = models.CharField("Aktion",max_length=20)
    at = models.DateTimeField("Zeitpunkt",auto_now_add=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['student','key'],name='stamp_idempotency')]

class Leave(models.Model):
    student = models.ForeignKey(USER,verbose_name="Student",on_delete=models.PROTECT)
    start = models.DateField('Beginn')
    end = models.DateField('Ende')
    comment = models.TextField('Kommentar',blank=True,max_length=2000)
    status = models.CharField('Status',max_length=12,default='pending',choices=[('pending','Beantragt'),('approved','Genehmigt'),('rejected','Abgelehnt'),('withdrawn','Zurückgezogen / aufgehoben')])
    decided_by = models.ForeignKey(USER,verbose_name='Entschieden von',on_delete=models.PROTECT,null=True,related_name='+')
    decided_at = models.DateTimeField("Entschieden am",null=True)
    reason = models.TextField("Begründung",blank=True)
    class Meta:
        verbose_name='Urlaubsantrag'; verbose_name_plural='Urlaubsanträge'
        constraints=[models.CheckConstraint(condition=Q(end__gte=F('start')),name='leave_order')]

class Audit(models.Model):
    actor = models.ForeignKey(USER,verbose_name="Bearbeiter",on_delete=models.PROTECT)
    at = models.DateTimeField("Zeitpunkt",auto_now_add=True)
    kind = models.CharField("Art",max_length=30)
    object_id = models.PositiveBigIntegerField("Objektkennung")
    before = models.JSONField("Ursprünglicher Wert")
    after = models.JSONField("Neuer Wert")
    reason = models.TextField("Begründung")
    class Meta: verbose_name='Änderungsprotokoll'; verbose_name_plural='Änderungsprotokolle'

class Report(models.Model):
    month = models.DateField('Berichtsmonat')
    recipient = models.EmailField('Empfänger')
    revision = models.PositiveIntegerField("Fassung",default=0)
    snapshot = models.JSONField("Gespeicherter Berichtsstand")
    status = models.CharField('Status',max_length=12,default='pending',choices=[('pending','Ausstehend'),('sending','Zustellung unklar'),('sent','Versandt'),('failed','Eindeutig fehlgeschlagen'),('unknown','Zustellung unklar')])
    created_at = models.DateTimeField("Zeitpunkt",auto_now_add=True)
    sent_at = models.DateTimeField("Versandt am",null=True)
    error = models.TextField("Fehler",blank=True)
    message_id = models.UUIDField("Versandkennung",default=uuid.uuid4,unique=True)
    reason = models.TextField("Begründung",blank=True)
    created_by = models.ForeignKey(USER,verbose_name="Erstellt von",on_delete=models.PROTECT,null=True,related_name='+')
    class Meta:
        verbose_name='Monatsbericht'; verbose_name_plural='Monatsberichte'
        constraints=[models.UniqueConstraint(fields=['month','recipient','revision'],name='unique_report_delivery')]

# The portable SQLite edition uses equivalent overlap triggers in its own migration.
# PostgreSQL retains its original exclusion constraints and original migrations.
if getattr(settings,'PORTABLE',False):
    for model in (Segment,Interval,Pause):
        constraints=[c for c in model._meta.constraints if not isinstance(c,ExclusionConstraint)]
        model._meta.constraints=constraints
        model._meta.original_attrs['constraints']=constraints
