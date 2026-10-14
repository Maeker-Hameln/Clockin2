import calendar,csv,io,smtplib
from datetime import date,timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives,get_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .calculations import dates,day_value,sums,FIELDS
from .models import Report,Policy
from .services import admin

def duration(value):
    if value is None: return 'nicht festgelegt'
    sign='−' if value<0 else ''
    minutes=abs(value)//60
    return f'{sign}{minutes//60}:{minutes%60:02d}'

@transaction.atomic
def snapshot(month):
    Policy.objects.select_for_update().get(pk=1)
    end=date(month.year,month.month,calendar.monthrange(month.year,month.month)[1])
    students=[]
    users=get_user_model().objects.filter(Q(is_staff=False)|Q(interval__isnull=False)|Q(plan__isnull=False)|Q(leave__isnull=False)).values_list('pk',flat=True).distinct()
    ids=list(users)
    # Same lock order as plan/leave approval: policy first, then users.
    # Corrections cannot alter half of a student's monthly snapshot.
    for student in get_user_model().objects.filter(pk__in=ids).select_for_update().order_by('username'):
        rows=[day_value(student,d) for d in dates(month,end)]
        if not student.is_active and not any(r['intervals'] or r['target'] is not None for r in rows): continue
        open_bookings={}
        for row in rows:
            for item in row['intervals']:
                if item['end'] is None:
                    entry=open_bookings.setdefault(item['id'],{'id':item['id'],'start':item['start'],'dates':[]})
                    entry['dates'].append(row['date'])
        students.append({'open_bookings':list(open_bookings.values()),'missing_plan_days':[r['date'] for r in rows if r['target'] is None or 'Planwerte fehlen' in r['issues']],'id':student.pk,'name':student.get_full_name() or student.username,'days':rows,'sums':sums(rows),'issues':[{'date':r['date'],'messages':r['issues']} for r in rows if r['issues']]})
    return {'month':month.isoformat(),'generated_at':timezone.now().isoformat(),'students':students,'incomplete':any(s['issues'] for s in students)}

@transaction.atomic
def prepare(month,recipient,actor=None,reason=''):
    if month.day!=1: raise ValidationError('Monat muss am ersten Kalendertag beginnen.')
    if month>=timezone.localdate().replace(day=1): raise ValidationError('Nur vollständig vergangene Monate berichten.')
    # The settings row serializes regular creation and explicit correction revisions.
    Policy.objects.select_for_update().get(pk=1)
    existing=Report.objects.filter(month=month,recipient=recipient)
    if actor is None:
        # A recipient change is prospective; never replay all historical months.
        old=Report.objects.filter(month=month,revision=0).order_by('created_at','pk').first()
        if old: return old
        revision=0
    else:
        admin(actor)
        if not reason.strip(): raise ValidationError('Korrektur benötigt eine Begründung.')
        revision=max([r.revision for r in existing],default=0)+1
    return Report.objects.create(month=month,recipient=recipient,revision=revision,snapshot=snapshot(month),reason=reason,created_by=actor)

def csv_content(data):
    stream=io.StringIO(newline='')
    writer=csv.writer(stream,delimiter=';',lineterminator='\r\n')
    writer.writerow(['Student','Datum','Phase','Soll','Nettoarbeit','Pause','Hochschule','Urlaub','Feiertag','Gesamt','Differenz','Hinweise'])
    for student in data['students']:
        for row in student['days']:
            # Protect spreadsheet consumers from formula interpretation of user-controlled names.
            name=student['name']
            if name.lstrip()[:1] in '=+-@' or name[:1] in '\t\r\n': name="'"+name
            writer.writerow([name,date.fromisoformat(row['date']).strftime('%d.%m.%Y'),row['phase'],*[duration(row[k]) for k in FIELDS],', '.join(row['issues'])])
    return '\ufeff'+stream.getvalue()

def message(report,connection):
    from django.template.loader import render_to_string
    data=report.snapshot
    context={'report':report,'data':data}
    subject=f"{'KORREKTUR – ' if report.revision else ''}{'VORLÄUFIG – ' if data['incomplete'] else ''}Zeiterfassung {report.month:%m.%Y}"
    body=render_to_string('tracker/report_mail.txt',context)
    msg=EmailMultiAlternatives(subject,body,settings.DEFAULT_FROM_EMAIL,[report.recipient],connection=connection,headers={'Message-ID':f'<{report.message_id}@clockin2.internal>'})
    msg.attach_alternative(render_to_string('tracker/report_mail.html',context),'text/html')
    msg.attach(f'Zeiterfassung-{report.month:%Y-%m}.csv',csv_content(data).encode('utf-8'),'text/csv; charset=utf-8')
    return msg

def deliver(report_id):
    with transaction.atomic():
        report=Report.objects.select_for_update().get(pk=report_id)
        if report.status not in ['pending','failed']: return report.status
        report.status='sending';report.error='Versand begonnen; bei Prozessabbruch Zustellung prüfen.';report.save(update_fields=['status','error'])
    connection=get_connection()
    try:
        connection.open()
    except Exception as exc:
        Report.objects.filter(pk=report_id).update(status='failed',error=f'Verbindung vor Versand fehlgeschlagen: {type(exc).__name__}')
        return 'failed'
    try:
        count=message(report,connection).send(fail_silently=False)
        if count!=1: raise RuntimeError('Keine eindeutige Versandbestätigung')
    except (smtplib.SMTPRecipientsRefused,smtplib.SMTPSenderRefused,smtplib.SMTPDataError) as exc:
        Report.objects.filter(pk=report_id).update(status='failed',error=f'SMTP hat Nachricht abgelehnt: {type(exc).__name__}')
        return 'failed'
    except Exception as exc:
        Report.objects.filter(pk=report_id).update(status='unknown',error=f'Zustellung prüfen; keine automatische Wiederholung: {type(exc).__name__}')
        return 'unknown'
    else:
        Report.objects.filter(pk=report_id).update(status='sent',sent_at=timezone.now(),error='')
        return 'sent'
    finally:
        try: connection.close()
        except Exception: pass

def run_due(now=None):
    now=timezone.localtime(now or timezone.now())
    policy=Policy.objects.filter(pk=1).first()
    if not policy or not policy.recipient or not policy.report_from:
        raise ValidationError('Berichtsempfänger und erster Berichtsmonat fehlen.')
    if not settings.DEFAULT_FROM_EMAIL: raise ValidationError('Absenderadresse fehlt.')
    month=policy.report_from
    results=[]
    while month<now.date().replace(day=1):
        next_month=(month.replace(day=28)+timedelta(days=4)).replace(day=1)
        due=timezone.make_aware(__import__('datetime').datetime.combine(next_month,__import__('datetime').time(7)))
        if now>=due:
            report=prepare(month,policy.recipient)
            results.append((report.pk,deliver(report.pk)))
        month=next_month
    return results
