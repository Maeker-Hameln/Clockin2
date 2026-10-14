import hashlib,json
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import Interval,Pause,Stamp,Leave,Plan,Segment,Audit,Policy,validate_week
from .calculations import dates,bounds,day_value,UTC
User=get_user_model()

def admin(actor):
    if not actor.is_active or not actor.is_staff: raise PermissionDenied

def lock(student):
    return User.objects.select_for_update().get(pk=student.pk)

@transaction.atomic
def stamp(student,action,key,now=None):
    lock(student)
    previous=Stamp.objects.filter(student=student,key=key).first()
    if previous:
        if previous.action!=action: raise ValidationError('Veraltete Aktion. Seite erneut laden.')
        return
    now=now or timezone.now()
    interval=Interval.objects.filter(student=student,end=None).first()
    if action=='in':
        if interval: raise ValidationError('Du bist bereits eingestempelt.')
        if Leave.objects.filter(student=student,status='approved',start__lte=timezone.localdate(now),end__gte=timezone.localdate(now)).exists():
            raise ValidationError('Für heute ist Urlaub genehmigt. Bitte zuerst administrativ klären.')
        Interval.objects.create(student=student,start=now)
    else:
        if not interval: raise ValidationError('Keine laufende Buchung vorhanden.')
        pause=interval.pauses.filter(end=None).first()
        if action=='pause':
            if pause: raise ValidationError('Die Pause läuft bereits.')
            Pause.objects.create(interval=interval,start=now)
        elif action=='resume':
            if not pause: raise ValidationError('Keine laufende Pause vorhanden.')
            pause.end=now; pause.save(update_fields=['end'])
        elif action=='out':
            if now<=interval.start: raise ValidationError('Ungültige Ausstempelzeit.')
            if pause: pause.end=now; pause.save(update_fields=['end'])
            interval.end=now; interval.save(update_fields=['end'])
        else: raise ValidationError('Unbekannte Aktion.')
    Stamp.objects.create(student=student,key=key,action=action)

@transaction.atomic
def decide_leave(actor,leave_id,status,reason=''):
    admin(actor)
    Policy.objects.select_for_update().get(pk=1)
    leave=Leave.objects.get(pk=leave_id)
    lock(leave.student)
    leave.refresh_from_db()
    if status not in ['approved','rejected','withdrawn']: raise ValidationError('Ungültiger Status.')
    if leave.status not in ['pending','approved'] or (leave.status=='approved' and status!='withdrawn'):
        raise ValidationError('Dieser Antrag kann nicht so geändert werden.')
    if not reason.strip(): raise ValidationError('Bitte eine Begründung eingeben.')
    if status=='approved': validate_leave(leave)
    before={'status':leave.status}
    leave.status=status;leave.decided_by=actor;leave.decided_at=timezone.now();leave.reason=reason;leave.save()
    Audit.objects.create(actor=actor,kind='leave',object_id=leave.pk,before=before,after={'status':status},reason=reason)

def validate_leave(leave):
    policy=Policy.objects.filter(pk=1,confirmed=True).first()
    if not policy: raise ValidationError('Betriebliche Regeln sind noch nicht bestätigt.')
    import holidays
    eligible=0
    for day in dates(leave.start,leave.end):
        row=day_value(leave.student,day)
        if row['target'] is None or row['university'] is None: raise ValidationError(f'{day:%d.%m.%Y}: Planwerte fehlen.')
        if row['target']==0: continue
        if day in holidays.Germany(subdiv=policy.state,years=day.year):
            raise ValidationError(f'{day:%d.%m.%Y}: Feiertag im Antrag – Zeitraum bitte aufteilen.')
        if row['target']==row['university']: raise ValidationError(f'{day:%d.%m.%Y}: Reiner Hochschultag.')
        if row['intervals']: raise ValidationError(f'{day:%d.%m.%Y}: Vorhandene oder offene Arbeitsbuchung.')
        if Leave.objects.filter(student=leave.student,status='approved',start__lte=day,end__gte=day).exclude(pk=leave.pk).exists(): raise ValidationError('Überschneidung mit genehmigtem Urlaub.')
        eligible+=1
    if not eligible: raise ValidationError('Keine vorgesehenen betrieblichen Arbeitstage im Antrag.')

def plan_preview(plan):
    segments=list(plan.segments.order_by('start'))
    if not segments: raise ValidationError('Keine Planabschnitte vorhanden.')
    if sum((s.end-s.start).days+1 for s in segments)>1096: raise ValidationError('Eine Fassung darf höchstens drei Jahre an Tageswerten enthalten.')
    previous=None
    rows=[]
    for s in segments:
        s.clean()
        if s.end-s.start>timedelta(days=730): raise ValidationError('Ein Abschnitt darf höchstens zwei Jahre umfassen.')
        if previous and s.start<=previous: raise ValidationError('Planabschnitte überschneiden sich.')
        previous=s.end
        if any(d['target'] is None or d['university'] is None for d in s.week): raise ValidationError('Fehlende Tageswerte müssen ausdrücklich ergänzt werden, auch freie Tage (0).')
        if s.uncertainties.strip(): raise ValidationError('Unklarheiten vor Aktivierung auflösen.')
        for day in dates(s.start,s.end):
            before=day_value(plan.student,day)
            after=day_value(plan.student,day,s)
            rows.append({'date':day.isoformat(),'before':before,'after':after})
    payload={'rows':rows,'sections':[{'id':s.pk,'start':str(s.start),'end':str(s.end),'week':s.week,'phase':s.phase} for s in segments]}
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    token=signing.dumps({'plan':plan.pk,'digest':digest},salt='activation')
    return rows,token,digest

@transaction.atomic
def activate(actor,plan_id,token,reason):
    admin(actor)
    policy=Policy.objects.select_for_update().get(pk=1)
    if not policy.confirmed or not policy.state: raise ValidationError('Betriebliche Anrechnungsregeln müssen vor Aktivierung bestätigt sein.')
    plan=Plan.objects.get(pk=plan_id)
    lock(plan.student)
    plan.refresh_from_db()
    if plan.status!='submitted': raise ValidationError('Nur eingereichte Fassungen können aktiviert werden.')
    if not reason.strip(): raise ValidationError('Eine Begründung ist erforderlich, insbesondere rückwirkend.')
    try: signed=signing.loads(token,salt='activation',max_age=1800)
    except signing.BadSignature: raise ValidationError('Vorschau abgelaufen. Bitte erneut prüfen.')
    rows,_,digest=plan_preview(plan)
    if signed!={'plan':plan.pk,'digest':digest}: raise ValidationError('Daten seit Vorschau geändert. Vorschau erneut laden.')
    old=[]
    for new in plan.segments.order_by('start'):
        for existing in list(Segment.objects.filter(student=plan.student,active=True,start__lte=new.end,end__gte=new.start)):
            old.append({'id':existing.pk,'plan':existing.plan_id,'start':str(existing.start),'end':str(existing.end),'week':existing.week})
            existing.active=False;existing.save(update_fields=['active'])
            for start,end in [(existing.start,min(existing.end,new.start-timedelta(days=1))),(max(existing.start,new.end+timedelta(days=1)),existing.end)]:
                if start<=end:
                    Segment.objects.create(plan=existing.plan,student=plan.student,start=start,end=end,week=existing.week,phase=existing.phase,active=True)
        new.active=True;new.save(update_fields=['active'])
    plan.status='active';plan.activated_by=actor;plan.activated_at=timezone.now();plan.reason=reason;plan.save()
    Audit.objects.create(actor=actor,kind='plan',object_id=plan.pk,before={'segments':old},after={'preview':rows},reason=reason)

def interval_snapshot(interval):
    return {'start':interval.start.astimezone(UTC).isoformat(),'end':interval.end.astimezone(UTC).isoformat() if interval.end else None,'pauses':[{'start':p.start.astimezone(UTC).isoformat(),'end':p.end.astimezone(UTC).isoformat() if p.end else None} for p in interval.pauses.order_by('start')]}

@transaction.atomic
def correct_interval(actor,interval_id,start,end,pauses,reason,expected,student=None):
    admin(actor)
    if interval_id is None:
        if student is None or student.is_staff: raise ValidationError('Student fehlt.')
        lock(student)
        interval=Interval(student=student,start=start,end=end)
        before={}
    else:
        interval=Interval.objects.get(pk=interval_id)
        lock(interval.student)
        interval.refresh_from_db()
        before=interval_snapshot(interval)
    if before!=expected: raise ValidationError('Buchung inzwischen geändert. Erneut laden.')
    if not reason.strip(): raise ValidationError('Begründung erforderlich.')
    if end and end<=start: raise ValidationError('Ende muss nach Beginn liegen.')
    qs=Interval.objects.filter(student=interval.student).exclude(pk=interval.pk)
    from django.db.models import Q
    if end: qs=qs.filter(start__lt=end)
    if qs.filter(Q(end__gt=start)|Q(end__isnull=True)).exists(): raise ValidationError('Arbeitsintervalle dürfen sich nicht überschneiden.')
    prev=start
    for ps,pe in pauses:
        if ps<prev or (pe and pe<ps) or (end and (pe is None or pe>end)):
            raise ValidationError('Pausen müssen innerhalb des Intervalls liegen und dürfen sich nicht überschneiden.')
        if pe is None and (ps,pe)!=pauses[-1]: raise ValidationError('Nur letzte Pause darf offen sein.')
        prev=pe or ps
    interval.start=start;interval.end=end;interval.save()
    # Existing pauses remain in the audit; replacement is explicit, not an unaudited admin edit.
    interval.pauses.all().delete()
    for ps,pe in pauses: Pause.objects.create(interval=interval,start=ps,end=pe)
    Audit.objects.create(actor=actor,kind='interval',object_id=interval.pk,before=before,after=interval_snapshot(interval),reason=reason)
