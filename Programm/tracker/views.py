import json,uuid
from datetime import date,timedelta
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required,user_passes_test
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.http import FileResponse,HttpResponse,HttpResponseBadRequest
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Plan,Segment,Interval,Leave,Report,Audit
from .forms import UploadForm,SegmentFormSet,LeaveForm,ReasonForm,CorrectionForm
from .calculations import day_value,dates,sums
from .ocr import recognize
from . import services,reports
staff_required=user_passes_test(lambda u:u.is_active and u.is_staff)

def notify_error(request,exc): messages.error(request,'; '.join(exc.messages))
def accessible_plan(request,pk):
    qs=Plan.objects.all() if request.user.is_staff else Plan.objects.filter(student=request.user)
    return get_object_or_404(qs,pk=pk)

@login_required
def home(request):
    day=timezone.localdate()
    interval=Interval.objects.filter(student=request.user,end=None).prefetch_related('pauses').first()
    pause=interval.pauses.filter(end=None).first() if interval else None
    row=day_value(request.user,day)
    provisional=0
    if interval:
        from .calculations import seconds,bounds
        lo,hi=bounds(day);now=timezone.now()
        provisional=seconds(interval.start,now,lo,hi)-sum(seconds(p.start,p.end or now,lo,hi) for p in interval.pauses.all())
    return render(request,'tracker/home.html',{'day':day,'interval':interval,'pause':pause,'row':row,'provisional':provisional,'today_total':row['work']+provisional,'stamp_key':str(uuid.uuid4())})

@login_required
@require_POST
def punch(request):
    if request.user.is_staff: raise PermissionDenied('Administratoren stempeln nicht als Student.')
    try: services.stamp(request.user,request.POST.get('action'),uuid.UUID(request.POST.get('key','')))
    except (ValueError,TypeError): return HttpResponseBadRequest('Ungültige Buchungskennung.')
    except ValidationError as exc: notify_error(request,exc)
    return redirect('home')

@login_required
def week(request):
    today=timezone.localdate().isocalendar()
    try:
        year=int(request.GET.get('year',today.year));number=int(request.GET.get('week',today.week))
        start=date.fromisocalendar(year,number,1)
    except ValueError: return HttpResponseBadRequest('Ungültiges ISO-Wochenjahr oder Kalenderwoche.')
    student=request.user
    if request.GET.get('student'):
        if not request.user.is_staff: raise PermissionDenied
        try: student_id=int(request.GET['student'])
        except ValueError: return HttpResponseBadRequest('Ungültige Studentenkennung.')
        student=get_object_or_404(get_user_model(),pk=student_id)
    rows=[day_value(student,d) for d in dates(start,start+timedelta(days=6))]
    for row in rows: row['day']=date.fromisoformat(row['date'])
    return render(request,'tracker/week.html',{'rows':rows,'totals':sums(rows),'incomplete':any(r['issues'] for r in rows),'student':student,'year':year,'number':number})

@login_required
def profile(request):
    upload=UploadForm();leave_form=LeaveForm()
    if request.method=='POST':
        if request.user.is_staff: raise PermissionDenied
        if request.POST.get('action')=='upload':
            upload=UploadForm(request.POST,request.FILES)
            if upload.is_valid():
                plan=Plan.objects.create(student=request.user,document=upload.cleaned_data['document'])
                sections,recognition=recognize(plan.document.path)
                plan.recognition=recognition;plan.save(update_fields=['recognition'])
                for section in sections: Segment.objects.create(plan=plan,student=request.user,**section)
                return redirect('plan',pk=plan.pk)
        elif request.POST.get('action')=='manual':
            plan=Plan.objects.create(student=request.user,recognition='Manuelle Erfassung – keine Stundenwerte vorgegeben.')
            return redirect('plan',pk=plan.pk)
        elif request.POST.get('action')=='leave':
            leave_form=LeaveForm(request.POST)
            if leave_form.is_valid():
                leave=leave_form.save(commit=False);leave.student=request.user;leave.save()
                messages.success(request,'Urlaubsantrag eingereicht. Die Verwaltung prüft Plandaten und Überschneidungen.')
                return redirect('profile')
    return render(request,'tracker/profile.html',{'upload':upload,'leave_form':leave_form,'plans':Plan.objects.filter(student=request.user).order_by('-pk'),'leaves':Leave.objects.filter(student=request.user).order_by('-pk')})

@login_required
@require_POST
def withdraw(request,pk):
    with transaction.atomic():
        services.lock(request.user)
        leave=get_object_or_404(Leave,pk=pk,student=request.user,status='pending')
        leave.status='withdrawn';leave.save(update_fields=['status'])
        Audit.objects.create(actor=request.user,kind='leave',object_id=leave.pk,before={'status':'pending'},after={'status':'withdrawn'},reason='Vom Studenten zurückgezogen')
    return redirect('profile')

@login_required
def plan_edit(request,pk):
    plan=accessible_plan(request,pk)
    editable=plan.status=='draft' and (request.user==plan.student or request.user.is_staff)
    initial=[{'start':s.start,'end':s.end,'phase':s.phase,'week':s.week,'uncertainties':s.uncertainties} for s in plan.segments.order_by('start')]
    formset=SegmentFormSet(request.POST or None,initial=initial)
    if request.method=='POST':
        if not editable: raise PermissionDenied
        if formset.is_valid():
            values=[f for f in formset if f.cleaned_data and not f.cleaned_data.get('DELETE')]
            ordered=sorted(values,key=lambda f:f.cleaned_data['start'])
            if any(a.cleaned_data['end']>=b.cleaned_data['start'] for a,b in zip(ordered,ordered[1:])):
                messages.error(request,'Planabschnitte dürfen sich nicht überschneiden.')
            elif not values: messages.error(request,'Mindestens einen Abschnitt erfassen.')
            else:
                with transaction.atomic():
                    services.lock(plan.student);plan.refresh_from_db()
                    if plan.status!='draft': raise PermissionDenied
                    before=[{'start':str(s.start),'end':str(s.end),'phase':s.phase,'week':s.week,'uncertainties':s.uncertainties} for s in plan.segments.order_by('start')]
                    plan.segments.all().delete()
                    for f in values:
                        c=f.cleaned_data
                        Segment.objects.create(plan=plan,student=plan.student,start=c['start'],end=c['end'],phase=c['phase'],week=f.week(),uncertainties=c['uncertainties'])
                    if request.POST.get('action')=='submit': plan.status='submitted';plan.save(update_fields=['status'])
                    after=[{'start':str(s.start),'end':str(s.end),'phase':s.phase,'week':s.week,'uncertainties':s.uncertainties} for s in plan.segments.order_by('start')]
                    Audit.objects.create(actor=request.user,kind='plan_draft',object_id=plan.pk,before={'segments':before},after={'segments':after,'status':plan.status},reason='Entwurf gespeichert bzw. ausdrücklich zur Prüfung eingereicht')
                messages.success(request,'Zur Prüfung eingereicht.' if plan.status=='submitted' else 'Entwurf gespeichert.')
                return redirect('plan',pk=pk)
    return render(request,'tracker/plan.html',{'plan':plan,'formset':formset,'editable':editable,'sections':plan.segments.order_by('start')})

@login_required
@require_POST
def copy_plan(request,pk):
    old=accessible_plan(request,pk)
    with transaction.atomic():
        services.lock(old.student)
        plan=Plan.objects.create(student=old.student,recognition=f'Neue Fassung aus Plan #{old.pk}. Nur gewünschte Zeiträume übernehmen.')
        for s in old.segments.filter(active=True):
            Segment.objects.create(plan=plan,student=old.student,start=s.start,end=s.end,phase=s.phase,week=s.week,uncertainties=s.uncertainties)
    return redirect('plan',pk=plan.pk)

@login_required
def download(request,pk):
    plan=accessible_plan(request,pk)
    if not plan.document: return HttpResponseBadRequest('Kein Dokument vorhanden.')
    response=FileResponse(plan.document.open('rb'),as_attachment=True,filename=plan.document.name.rsplit('/',1)[-1])
    response['Cache-Control']='private, no-store'
    return response

@login_required
@staff_required
def dashboard(request):
    return render(request,'tracker/dashboard.html',{'plans':Plan.objects.filter(status='submitted').select_related('student'),'leaves':Leave.objects.filter(status__in=['pending','approved']).select_related('student'),'students':get_user_model().objects.filter(is_staff=False),'reports':Report.objects.order_by('-month','-revision')[:100],'intervals':Interval.objects.select_related('student').order_by('-start')[:100]})

@login_required
@staff_required
def review(request,pk):
    plan=get_object_or_404(Plan,pk=pk,status='submitted')
    form=ReasonForm(request.POST or None)
    rows=[];token=''
    try:
        rows,token,_=services.plan_preview(plan)
        if request.method=='POST' and form.is_valid():
            services.activate(request.user,pk,request.POST.get('token',''),form.cleaned_data['reason'])
            messages.success(request,'Plan für die ausdrücklich angegebenen Zeiträume aktiviert.')
            return redirect('dashboard')
    except ValidationError as exc: notify_error(request,exc)
    return render(request,'tracker/review.html',{'plan':plan,'rows':rows,'token':token,'form':form})

@login_required
@staff_required
@require_POST
def return_plan(request,pk):
    with transaction.atomic():
        plan=get_object_or_404(Plan,pk=pk);services.lock(plan.student);plan.refresh_from_db()
        if plan.status!='submitted': return HttpResponseBadRequest('Plan nicht in Prüfung.')
        reason=request.POST.get('reason','').strip()
        if not reason: return HttpResponseBadRequest('Begründung fehlt.')
        plan.status='draft';plan.save(update_fields=['status'])
        Audit.objects.create(actor=request.user,kind='plan_return',object_id=pk,before={'status':'submitted'},after={'status':'draft'},reason=reason)
    messages.success(request,'Plan zur Korrektur zurückgegeben.')
    return redirect('dashboard')

@login_required
@staff_required
@require_POST
def leave_decision(request,pk):
    try: services.decide_leave(request.user,pk,request.POST.get('status'),request.POST.get('reason',''))
    except ValidationError as exc: notify_error(request,exc)
    return redirect('dashboard')

@login_required
@staff_required
def correct(request,pk):
    interval=get_object_or_404(Interval,pk=pk)
    before=services.interval_snapshot(interval)
    initial={'start':timezone.localtime(interval.start).strftime('%d.%m.%Y %H:%M:%S'),'end':timezone.localtime(interval.end).strftime('%d.%m.%Y %H:%M:%S') if interval.end else '', 'pauses':'\n'.join(f"{timezone.localtime(p.start):%d.%m.%Y %H:%M:%S} | {timezone.localtime(p.end).strftime('%d.%m.%Y %H:%M:%S') if p.end else ''}" for p in interval.pauses.order_by('start')),'expected':signing.dumps(before,salt='correction')}
    form=CorrectionForm(request.POST or None,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:
            data=form.cleaned_data
            expected=signing.loads(data['expected'],salt='correction',max_age=3600)
            services.correct_interval(request.user,pk,data['start'],data['end'],data['pauses'],data['reason'],expected)
            messages.success(request,'Korrektur mit ursprünglichen Werten protokolliert.')
            return redirect('dashboard')
        except signing.BadSignature: messages.error(request,'Formular abgelaufen. Erneut laden.')
        except ValidationError as exc: notify_error(request,exc)
    return render(request,'tracker/correct.html',{'form':form,'interval':interval})

@login_required
@staff_required
def report_detail(request,pk):
    report=get_object_or_404(Report,pk=pk)
    return render(request,'tracker/report.html',{'report':report,'data':report.snapshot,'form':ReasonForm()})

@login_required
@staff_required
def report_csv(request,pk):
    report=get_object_or_404(Report,pk=pk)
    response=HttpResponse(reports.csv_content(report.snapshot),content_type='text/csv; charset=utf-8')
    response['Content-Disposition']=f'attachment; filename="Zeiterfassung-{report.month:%Y-%m}-v{report.revision}.csv"'
    return response

@login_required
@staff_required
@require_POST
def report_action(request,pk):
    report=get_object_or_404(Report,pk=pk)
    if request.POST.get('action')=='retry':
        if report.status!='failed': return HttpResponseBadRequest('Nur eindeutig fehlgeschlagene Zustellungen wiederholen.')
        reports.deliver(pk);report.refresh_from_db();messages.info(request,f'Versandstatus: {report.get_status_display()}')
    elif request.POST.get('action')=='correction':
        try:
            new=reports.prepare(report.month,report.recipient,request.user,request.POST.get('reason',''))
            reports.deliver(new.pk)
            return redirect('report',pk=new.pk)
        except ValidationError as exc: notify_error(request,exc)
    else: return HttpResponseBadRequest('Unbekannte Aktion.')
    return redirect('report',pk=pk)

@login_required
@staff_required
def new_interval(request,pk):
    student=get_object_or_404(get_user_model(),pk=pk,is_staff=False)
    form=CorrectionForm(request.POST or None,initial={'expected':'new'})
    if request.method=='POST' and form.is_valid():
        try:
            d=form.cleaned_data
            services.correct_interval(request.user,None,d['start'],d['end'],d['pauses'],d['reason'],{},student)
            messages.success(request,'Vergessene Buchung mit Begründung nachgetragen.')
            return redirect('dashboard')
        except ValidationError as exc: notify_error(request,exc)
    return render(request,'tracker/correct.html',{'form':form,'interval':{'student':student,'pk':'neu'}})

@login_required
@staff_required
def new_plan(request,pk):
    student=get_object_or_404(get_user_model(),pk=pk,is_staff=False)
    upload=UploadForm(request.POST or None,request.FILES or None)
    if request.method=='POST' and (request.POST.get('action')=='manual' or upload.is_valid()):
        plan=Plan.objects.create(student=student,recognition='Manuelle Erfassung durch Verwaltung. Keine Tageswerte vorgegeben.')
        if request.POST.get('action')!='manual':
            plan.document=upload.cleaned_data['document'];plan.save()
            sections,recognition=recognize(plan.document.path)
            plan.recognition=recognition;plan.save(update_fields=['recognition'])
            for section in sections: Segment.objects.create(plan=plan,student=student,**section)
        return redirect('plan',pk=plan.pk)
    return render(request,'tracker/admin_upload.html',{'student':student,'upload':upload})
