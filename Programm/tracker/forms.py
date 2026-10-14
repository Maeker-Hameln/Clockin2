from datetime import timedelta
from django import forms
from django.core.exceptions import ValidationError
from .models import Leave,PHASES
from .ocr import validate_document
DAYS=['Montag','Dienstag','Mittwoch','Donnerstag','Freitag','Samstag','Sonntag']
class DateInput(forms.DateInput):
    input_type='date'
    def __init__(self,*args,**kwargs): super().__init__(*args,format='%Y-%m-%d',**kwargs)
class UploadForm(forms.Form):
    document=forms.FileField(label='Hochschulablaufplan (PDF, JPG oder PNG, maximal 10 MB)')
    def clean_document(self):
        file=self.cleaned_data['document'];validate_document(file);return file
class MinuteField(forms.CharField):
    def __init__(self,**kwargs): super().__init__(required=False,help_text='Stunden:Minuten, z. B. 8:00; leer = unbekannt',**kwargs)
    def to_python(self,value):
        if not value: return None
        try:
            h,m=value.strip().split(':');h=int(h);m=int(m)
            if h<0 or not 0<=m<60 or h*60+m>1440: raise ValueError
            return h*60+m
        except (ValueError,TypeError): raise ValidationError('Bitte Stunden:Minuten zwischen 0:00 und 24:00 eingeben.')
    def prepare_value(self,value):
        return f'{value//60}:{value%60:02}' if type(value)==int else value
class SegmentForm(forms.Form):
    start=forms.DateField(label='Beginn (einschließlich)',widget=DateInput())
    end=forms.DateField(label='Ende (einschließlich)',widget=DateInput())
    phase=forms.ChoiceField(label='Phase',choices=PHASES)
    uncertainties=forms.CharField(label='Unklarheiten (vor Aktivierung auflösen)',required=False,widget=forms.Textarea(attrs={'rows':2}))
    def __init__(self,*args,**kwargs):
        initial=kwargs.get('initial',{})
        week=initial.pop('week',None)
        if week:
            for n,day in enumerate(week): initial[f'target_{n}']=day['target'];initial[f'university_{n}']=day['university']
        super().__init__(*args,**kwargs)
        for n,day in enumerate(DAYS):
            self.fields[f'target_{n}']=MinuteField(label=f'{day}: Soll')
            self.fields[f'university_{n}']=MinuteField(label=f'{day}: Hochschule')
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end'):
            if data['end']<data['start'] or data['end']-data['start']>timedelta(days=730): raise ValidationError('Zeitraum muss geordnet sein und höchstens zwei Jahre umfassen.')
        for n in range(7):
            t,c=data.get(f'target_{n}'),data.get(f'university_{n}')
            if t is not None and c is not None and c>t: raise ValidationError('Hochschulanrechnung darf Soll nicht übersteigen.')
        return data
    def week(self): return [{'target':self.cleaned_data.get(f'target_{n}'),'university':self.cleaned_data.get(f'university_{n}')} for n in range(7)]
SegmentFormSet=forms.formset_factory(SegmentForm,extra=1,can_delete=True,max_num=30,validate_max=True,absolute_max=31)
class LeaveForm(forms.ModelForm):
    class Meta:
        model=Leave;fields=['start','end','comment'];widgets={'start':DateInput(),'end':DateInput(),'comment':forms.Textarea(attrs={'rows':2})}
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and (data['end']<data['start'] or data['end']-data['start']>timedelta(days=366)):
            raise ValidationError('Urlaubszeitraum muss geordnet sein und höchstens ein Jahr umfassen.')
        return data
class ReasonForm(forms.Form):
    reason=forms.CharField(label='Begründung',widget=forms.Textarea(attrs={'rows':3}),max_length=4000)
class CorrectionForm(ReasonForm):
    start=forms.DateTimeField(label='Beginn (Europe/Berlin)',input_formats=['%d.%m.%Y %H:%M:%S','%d.%m.%Y %H:%M'],help_text='TT.MM.JJJJ HH:MM:SS; Bei Zeitumstellung eindeutigen ISO-Zeitstempel mit Offset eingeben, z. B. 2026-10-25T02:30:00+02:00.')
    end=forms.DateTimeField(label='Ende (leer = offen)',required=False,input_formats=['%d.%m.%Y %H:%M:%S','%d.%m.%Y %H:%M'])
    pauses=forms.CharField(label='Pausen',required=False,widget=forms.Textarea(),help_text='Pro Zeile: TT.MM.JJJJ HH:MM:SS | TT.MM.JJJJ HH:MM:SS. Offenes Ende leer lassen.')
    expected=forms.CharField(widget=forms.HiddenInput())
    def clean_pauses(self):
        values=[]
        for line in self.cleaned_data['pauses'].splitlines():
            try:
                a,b=line.split('|')
                field=self.fields['end']
                values.append((self.fields['start'].clean(a.strip()),field.clean(b.strip())))
            except (ValueError,ValidationError): raise ValidationError('Ungültige Pause. Datumsformat und Zeitzone prüfen.')
        return values
