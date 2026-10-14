from django import template
from tracker.reports import duration
from datetime import date
register=template.Library()
register.filter('duration',duration)
@register.filter
def german_date(value):
    try: return date.fromisoformat(value[:10]).strftime('%d.%m.%Y')
    except (ValueError,TypeError):return value

@register.filter
def minute_duration(value): return duration(value*60 if value is not None else None)
@register.filter
def weekday(value): return ['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag'][int(value)]

@register.filter
def german_datetime(value):
    from datetime import datetime
    from tracker.calculations import BERLIN
    try: return datetime.fromisoformat(value).astimezone(BERLIN).strftime('%d.%m.%Y %H:%M')
    except (ValueError,TypeError):return value
