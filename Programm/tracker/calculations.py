from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
import holidays
from django.db.models import Q
from django.utils import timezone
from .models import Segment, Interval, Leave, Policy
BERLIN=ZoneInfo('Europe/Berlin')
UTC=dt_timezone.utc

def dates(start,end):
    while start<=end:
        yield start
        start+=timedelta(days=1)

def bounds(day):
    return (datetime.combine(day,time.min,BERLIN).astimezone(UTC),datetime.combine(day+timedelta(days=1),time.min,BERLIN).astimezone(UTC))

def seconds(start,end,lo,hi):
    return max(0,int((min(end.astimezone(UTC),hi)-max(start.astimezone(UTC),lo)).total_seconds()))

def day_value(student,day,override=None):
    lo,hi=bounds(day)
    policy=Policy.objects.filter(pk=1).first()
    segment=override or Segment.objects.filter(student=student,active=True,start__lte=day,end__gte=day).first()
    result={'date':day.isoformat(),'phase':segment.get_phase_display() if segment else 'Nicht festgelegt','target':None,'work':0,'pause':0,'university':None,'leave':None,'holiday':None,'total':None,'delta':None,'issues':[],'intervals':[]}
    for interval in Interval.objects.filter(student=student,start__lt=hi).filter(Q(end__gt=lo)|Q(end__isnull=True)).prefetch_related('pauses'):
        if interval.end is None and lo>timezone.now(): continue
        result['intervals'].append({'id':interval.pk,'start':interval.start.astimezone(BERLIN).isoformat(),'end':interval.end.astimezone(BERLIN).isoformat() if interval.end else None})
        if interval.end is None:
            result['issues'].append('Offene Buchung')
            continue
        gross=seconds(interval.start,interval.end,lo,hi)
        pause=sum(seconds(p.start,p.end,lo,hi) for p in interval.pauses.all() if p.end)
        result['work']+=gross-pause
        result['pause']+=pause
    if not segment:
        result['issues'].append('Sollzeit nicht festgelegt')
        return result
    rule=segment.week[day.weekday()]
    if rule['target'] is not None: result['target']=rule['target']*60
    if rule['target'] is None or rule['university'] is None:
        result['issues'].append('Planwerte fehlen')
        return result
    if not policy or not policy.confirmed or not policy.state:
        result['issues'].append('Betriebliche Anrechnungsregeln nicht bestätigt')
        return result
    result.update(university=rule['university']*60,leave=0,holiday=0)
    is_holiday=day in holidays.Germany(subdiv=policy.state,years=day.year)
    approved=Leave.objects.filter(student=student,status='approved',start__lte=day,end__gte=day).exists()
    if is_holiday:
        result.update(holiday=result['target'],university=0)
    elif approved:
        if result['work'] or result['intervals'] or rule['target']==rule['university']>0:
            result['issues'].append('Urlaubskonflikt – administrativ klären')
            result['leave']=None
        else:
            result['leave']=(rule['target']-rule['university'])*60
    if result['leave'] is not None:
        result['total']=result['work']+result['university']+result['leave']+result['holiday']
        result['delta']=result['total']-result['target']
    return result

FIELDS=['target','work','pause','university','leave','holiday','total','delta']
def sums(rows):
    return {key:None if any(r[key] is None for r in rows) else sum(r[key] for r in rows) for key in FIELDS}
