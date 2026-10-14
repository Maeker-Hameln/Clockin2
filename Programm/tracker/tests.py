import io,json,uuid,tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta,timezone as UTC
from unittest.mock import patch,MagicMock
from zoneinfo import ZoneInfo
from pathlib import Path
from django.test import TestCase,TransactionTestCase,override_settings,Client
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction,IntegrityError,close_old_connections,connections
from PIL import Image,ImageDraw,ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from .models import *
from .services import stamp,activate,plan_preview,decide_leave,correct_interval,interval_snapshot
from .calculations import day_value,bounds,sums
from .reports import prepare,deliver,run_due,csv_content
from .ocr import recognize,validate_document,calendar_outline
BERLIN=ZoneInfo('Europe/Berlin')
User=get_user_model()
def at(day,hour,minute=0,fold=0):
    return datetime.combine(day,datetime.min.time()).replace(hour=hour,minute=minute,tzinfo=BERLIN,fold=fold)
def week(target=480,credit=0): return [{'target':target if n<5 else 0,'university':credit if n<5 else 0} for n in range(7)]

class Fixture:
    def setUp(self):
        self.student=User.objects.create_user('student','', 'Test-Passwort-2026!')
        self.other=User.objects.create_user('other','', 'Test-Passwort-2026!')
        self.admin=User.objects.create_user('admin','', 'Test-Passwort-2026!',is_staff=True)
        Policy.objects.update_or_create(pk=1,defaults={'state':'NI','confirmed':True,'rules':'Künstliche Testregel: Schule laut Plan; Urlaub betriebliches Restsoll; Feiertag Soll.','recipient':'test@example.invalid','report_from':date(2026,1,1)})
    def plan(self,start,end,target=480,credit=0,student=None,active=True):
        p=Plan.objects.create(student=student or self.student,status='active' if active else 'submitted')
        Segment.objects.create(plan=p,student=p.student,start=start,end=end,phase='practice' if credit==0 else 'mixed',week=week(target,credit),active=active)
        return p

class Plans(Fixture,TestCase):
    def test_A_explicit_period_does_not_change_other_months_or_year(self):
        self.plan(date(2026,1,1),date(2026,3,31))
        self.plan(date(2026,4,1),date(2026,6,30),240)
        self.plan(date(2026,7,1),date(2026,12,31))
        p=self.plan(date(2026,4,1),date(2026,4,30),180,active=False)
        rows,token,_=plan_preview(p)
        self.assertEqual(len(rows),30)
        activate(self.admin,p.pk,token,'Ausdrückliche Aprilkorrektur')
        for day,target in [(date(2026,1,12),480),(date(2026,4,13),180),(date(2026,5,11),240),(date(2026,7,13),480)]:
            self.assertEqual(day_value(self.student,day)['target'],target*60)
        self.assertIsNone(day_value(self.student,date(2027,1,4))['target'])
        self.assertEqual(Audit.objects.filter(kind='plan').count(),1)
    def test_B_plan_gap_is_unknown(self):
        self.plan(date(2026,1,1),date(2026,1,31))
        row=day_value(self.student,date(2026,2,2))
        self.assertIsNone(row['target']);self.assertIsNone(row['delta'])
        self.assertIn('Sollzeit nicht festgelegt',row['issues'])
    def test_overlap_database_rejects(self):
        self.plan(date(2026,1,1),date(2026,1,31))
        with self.assertRaises(IntegrityError),transaction.atomic():self.plan(date(2026,1,20),date(2026,2,1))
    def test_preview_changes_require_new_confirmation(self):
        p=self.plan(date(2026,4,1),date(2026,4,3),active=False)
        _,token,_=plan_preview(p)
        segment=p.segments.get();segment.week=week(240);segment.save()
        with self.assertRaises(ValidationError):activate(self.admin,p.pk,token,'Korrektur')
    def test_missing_hours_cannot_activate(self):
        p=self.plan(date(2026,4,1),date(2026,4,3),active=False)
        s=p.segments.get();s.week[0]['target']=None;s.save()
        with self.assertRaises(ValidationError):plan_preview(p)
    def test_unconfirmed_rules_are_unknown(self):
        self.plan(date(2026,4,1),date(2026,4,30))
        Policy.objects.filter(pk=1).update(confirmed=False)
        self.assertIsNone(day_value(self.student,date(2026,4,13))['total'])

class Times(Fixture,TestCase):
    def test_C_net_eight_hours(self):
        d=date(2026,4,13)
        interval=Interval.objects.create(student=self.student,start=at(d,8),end=at(d,16,30))
        Pause.objects.create(interval=interval,start=at(d,12),end=at(d,12,30))
        row=day_value(self.student,d)
        self.assertEqual(row['work'],8*3600);self.assertEqual(row['pause'],1800)
    def test_stamping_and_end_during_pause(self):
        d=date(2026,4,13)
        stamp(self.student,'in',uuid.uuid4(),at(d,8))
        stamp(self.student,'pause',uuid.uuid4(),at(d,12))
        stamp(self.student,'out',uuid.uuid4(),at(d,12,30))
        i=Interval.objects.get();self.assertEqual(i.end,i.pauses.get().end)
        self.assertEqual(day_value(self.student,d)['work'],4*3600)
    def test_no_fabricated_pause(self):
        d=date(2026,4,13)
        Interval.objects.create(student=self.student,start=at(d,8),end=at(d,16,30))
        self.assertEqual(day_value(self.student,d)['work'],8*3600+1800)
    def test_D_duplicate_click_and_new_session_preserve_open_state(self):
        key=uuid.uuid4();stamp(self.student,'in',key)
        stamp(self.student,'in',key)
        with self.assertRaises(ValidationError):stamp(self.student,'in',uuid.uuid4())
        self.assertEqual(Interval.objects.count(),1)
        c=Client();c.force_login(self.student)
        self.assertContains(c.get('/'),'Arbeitet')
        c.post('/abmeldung/')
        self.assertIsNone(Interval.objects.get().end)
        c=Client();c.force_login(self.student);self.assertContains(c.get('/'),'Arbeitet')
    def test_F_midnight_and_month_boundary(self):
        a=date(2026,1,31);b=date(2026,2,1)
        i=Interval.objects.create(student=self.student,start=at(a,23),end=at(b,2))
        Pause.objects.create(interval=i,start=at(a,23,45),end=at(b,0,15))
        self.assertEqual(day_value(self.student,a)['work'],45*60)
        self.assertEqual(day_value(self.student,b)['work'],105*60)
    def test_F_DST_elapsed_time_not_wall_clock(self):
        for day,expected in [(date(2026,3,29),3600),(date(2026,10,25),10800)]:
            Interval.objects.create(student=self.student,start=at(day,1,30),end=at(day,3,30))
            self.assertEqual(day_value(self.student,day)['work'],expected)
    def test_F_day_bounds_23_and_25_hours(self):
        for d,h in [(date(2026,3,29),23),(date(2026,10,25),25)]:
            a,b=bounds(d);self.assertEqual((b-a).total_seconds(),h*3600)
    def test_F_ISO_year_boundary(self):
        c=Client();c.force_login(self.student)
        response=c.get('/woche/?year=2020&week=53')
        self.assertContains(response,'28.12.2020');self.assertContains(response,'03.01.2021')
    def test_open_interval_excluded_from_final_work(self):
        d=date(2026,4,13)
        Interval.objects.create(student=self.student,start=at(d,8))
        row=day_value(self.student,d)
        self.assertEqual(row['work'],0);self.assertIn('Offene Buchung',row['issues'])
    def test_I_correction_audit_and_invalid_pause(self):
        d=date(2026,4,13)
        i=Interval.objects.create(student=self.student,start=at(d,8))
        before=interval_snapshot(i)
        correct_interval(self.admin,i.pk,at(d,8),at(d,16,30),[(at(d,12),at(d,12,30))],'Vergessen auszustempeln',before)
        log=Audit.objects.get();self.assertEqual(log.before,before);self.assertEqual(log.actor,self.admin)
        i.refresh_from_db()
        with self.assertRaises(ValidationError):correct_interval(self.admin,i.pk,i.start,i.end,[(at(d,7),at(d,8))],'Fehler',interval_snapshot(i))
    def test_admin_can_add_forgotten_booking(self):
        d=date(2026,4,13)
        correct_interval(self.admin,None,at(d,8),at(d,16),[],'Nachtrag',{},self.student)
        self.assertEqual(Audit.objects.get().before,{})
        self.assertEqual(Interval.objects.count(),1)
    def test_interval_overlap_is_rejected(self):
        d=date(2026,4,13)
        Interval.objects.create(student=self.student,start=at(d,8),end=at(d,12))
        with self.assertRaises(IntegrityError),transaction.atomic():
            Interval.objects.create(student=self.student,start=at(d,11),end=at(d,16))

class Leaves(Fixture,TestCase):
    def test_E_only_approved_and_separate_school_credit(self):
        d=date(2026,4,13)
        self.plan(d,d,480,120)
        l=Leave.objects.create(student=self.student,start=d,end=d)
        self.assertEqual(day_value(self.student,d)['leave'],0)
        decide_leave(self.admin,l.pk,'approved','Geprüft')
        row=day_value(self.student,d)
        self.assertEqual(row['leave'],360*60);self.assertEqual(row['university'],120*60)
        self.assertEqual(row['work'],0);self.assertEqual(row['total'],480*60)
    def test_school_only_leave_rejected(self):
        d=date(2026,4,13);self.plan(d,d,480,480)
        l=Leave.objects.create(student=self.student,start=d,end=d)
        with self.assertRaises(ValidationError):decide_leave(self.admin,l.pk,'approved','Geprüft')
    def test_booking_conflict_and_overlapping_leave_rejected(self):
        d=date(2026,4,13);self.plan(d,d)
        l=Leave.objects.create(student=self.student,start=d,end=d)
        Interval.objects.create(student=self.student,start=at(d,8),end=at(d,9))
        with self.assertRaises(ValidationError):decide_leave(self.admin,l.pk,'approved','Geprüft')
    def test_holiday_credit_exclusive_and_multi_year(self):
        for d in [date(2026,5,1),date(2027,5,6)]:
            self.plan(d,d,480,240)
            row=day_value(self.student,d)
            self.assertEqual(row['holiday'],480*60);self.assertEqual(row['university'],0)
    def test_approved_leave_blocks_stamp(self):
        d=date(2026,4,13);self.plan(d,d)
        l=Leave.objects.create(student=self.student,start=d,end=d)
        decide_leave(self.admin,l.pk,'approved','Geprüft')
        with self.assertRaises(ValidationError):stamp(self.student,'in',uuid.uuid4(),at(d,8))

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='test@example.invalid')
class Reports(Fixture,TestCase):
    def test_H_previous_calendar_month_exact_and_no_duplicate(self):
        month=date(2026,1,1)
        report=prepare(month,'test@example.invalid')
        self.assertEqual(len(report.snapshot['students'][0]['days']),31)
        self.assertEqual(report.snapshot['students'][0]['days'][-1]['date'],'2026-01-31')
        self.assertTrue(report.snapshot['incomplete'])
        self.assertEqual(deliver(report.pk),'sent');self.assertEqual(deliver(report.pk),'sent')
        self.assertEqual(prepare(month,'test@example.invalid').pk,report.pk)
        self.assertEqual(len(mail.outbox),1)
        self.assertIn('VORLÄUFIG',mail.outbox[0].subject)
        self.assertTrue(csv_content(report.snapshot).startswith('\ufeff'))
    def test_I_saved_snapshot_is_unchanged_after_plan_change(self):
        month=date(2026,1,1);old=prepare(month,'test@example.invalid');original=json.dumps(old.snapshot,sort_keys=True)
        self.plan(month,date(2026,1,31))
        new=prepare(month,'test@example.invalid',self.admin,'Korrektur nach Planfreigabe')
        old.refresh_from_db();self.assertEqual(json.dumps(old.snapshot,sort_keys=True),original)
        self.assertEqual(new.revision,1);self.assertEqual(new.created_by,self.admin)
    def test_unknown_delivery_not_automatically_retried(self):
        r=prepare(date(2026,1,1),'test@example.invalid')
        with patch('tracker.reports.message') as mock:
            mock.return_value.send.side_effect=TimeoutError()
            self.assertEqual(deliver(r.pk),'unknown')
            deliver(r.pk);self.assertEqual(mock.call_count,1)
    def test_clear_connection_failure_can_retry(self):
        r=prepare(date(2026,1,1),'test@example.invalid')
        with patch('tracker.reports.get_connection') as mock:
            mock.return_value.open.side_effect=ConnectionRefusedError()
            self.assertEqual(deliver(r.pk),'failed')
        self.assertEqual(deliver(r.pk),'sent')
    def test_schedule_7am_and_catchup(self):
        run_due(at(date(2026,2,1),6,59));self.assertEqual(Report.objects.count(),0)
        run_due(at(date(2026,2,1),7));self.assertEqual(Report.objects.count(),1)
        run_due(at(date(2026,4,2),8));self.assertEqual(Report.objects.count(),3)
        self.assertEqual(len(mail.outbox),3)
    def test_csv_name_formula_escaped(self):
        self.student.first_name='=HYPERLINK';self.student.save()
        r=prepare(date(2026,1,1),'test@example.invalid')
        self.assertIn("'=HYPERLINK",csv_content(r.snapshot))

class Permissions(Fixture,TestCase):
    def test_G_foreign_objects_and_admin_routes(self):
        p=self.plan(date(2026,4,1),date(2026,4,30),student=self.other)
        c=Client();c.force_login(self.student)
        for url in [f'/plan/{p.pk}/',f'/plan/{p.pk}/dokument/']:
            self.assertEqual(c.get(url).status_code,404)
        self.assertEqual(c.post(f'/plan/{p.pk}/neue-fassung/').status_code,404)
        self.assertEqual(c.get(f'/woche/?student={self.other.pk}').status_code,403)
        self.assertEqual(c.get('/verwaltung/').status_code,302)
        with self.assertRaises(PermissionDenied):decide_leave(self.student,1,'approved','Manipulation')
    def test_csrf_and_no_public_registration(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.student)
        self.assertEqual(c.post('/stempeln/',{'action':'in','key':str(uuid.uuid4())}).status_code,403)
        self.assertEqual(c.get('/registrierung/').status_code,404)
    def test_student_and_admin_pages_render(self):
        c=Client();c.force_login(self.student)
        for url in ['/','/woche/','/profil/']:self.assertEqual(c.get(url).status_code,200)
        p=Plan.objects.create(student=self.student)
        self.assertEqual(c.get(f'/plan/{p.pk}/').status_code,200)
        c.force_login(self.admin)
        for url in ['/verwaltung/','/admin/','/admin/auth/user/','/admin/tracker/policy/1/change/',f'/verwaltung/student/{self.student.pk}/nachtragen/']:
            self.assertEqual(c.get(url).status_code,200)
    def test_login_lockout(self):
        c=Client()
        for _ in range(6):response=c.post('/anmeldung/',{'username':self.student.username,'password':'falsch'})
        self.assertEqual(response.status_code,429)
    def test_submission_freezes_student_edits(self):
        p=self.plan(date(2026,4,1),date(2026,4,3),active=False)
        c=Client();c.force_login(self.student)
        self.assertEqual(c.post(f'/plan/{p.pk}/',{}).status_code,403)

class Documents(Fixture,TestCase):
    def test_upload_types_and_limits(self):
        file=SimpleUploadedFile('bad.png',b'<script>bad</script>')
        with self.assertRaises(ValidationError):validate_document(file)
        image=Image.new('RGB',(50,50),'white');stream=io.BytesIO();image.save(stream,'PNG')
        self.assertEqual(validate_document(SimpleUploadedFile('real.png',stream.getvalue())),'image')
        file=SimpleUploadedFile('large.pdf',b'x'*(10*1024*1024+1))
        with self.assertRaises(ValidationError):validate_document(file)
    def test_text_pdf_extraction_never_invents_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject();stream.set_data(b'BT /F1 16 Tf 30 700 Td (01.01.2025 - 31.03.2025 Praxis) Tj ET')
            page[NameObject('/Contents')]=writer._add_object(stream)
            path=Path(tmp)/'test.pdf';writer.write(path)
            sections,text=recognize(str(path))
            self.assertEqual(len(sections),1);self.assertEqual(sections[0]['start'],date(2025,1,1))
            self.assertIsNone(sections[0]['week'][0]['target'])
    def test_local_image_OCR(self):
        with tempfile.TemporaryDirectory() as tmp:
            image=Image.new('RGB',(1500,240),'white');draw=ImageDraw.Draw(image)
            font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',48)
            draw.text((30,70),'01.01.2025 - 31.03.2025 Praxis',font=font,fill='black')
            path=Path(tmp)/'plan.png';image.save(path)
            sections,text=recognize(str(path))
            self.assertEqual(len(sections),1,text)
            self.assertEqual(sections[0]['phase'],'practice')
    def test_ocr_failure_allows_manual_draft(self):
        with patch('tracker.ocr.ocr_image',side_effect=FileNotFoundError):
            sections,text=recognize('/missing.png')
            self.assertEqual(sections,[]);self.assertIn('manuell',text)

class Concurrency(Fixture,TransactionTestCase):
    def test_parallel_stamps_have_one_booking(self):
        pk=self.student.pk;key=uuid.uuid4()
        def punch(_):
            close_old_connections()
            try: stamp(User.objects.get(pk=pk),'in',key)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=4) as executor:list(executor.map(punch,range(4)))
        self.assertEqual(Interval.objects.filter(student_id=pk).count(),1)
        self.assertEqual(Stamp.objects.filter(student_id=pk).count(),1)

class AdminWorkflow(Fixture,TestCase):
    def test_admin_upload_goes_to_selected_student(self):
        with tempfile.TemporaryDirectory() as tmp,override_settings(MEDIA_ROOT=tmp):
            c=Client();c.force_login(self.admin)
            self.assertContains(c.get(f'/verwaltung/student/{self.other.pk}/plan/'),self.other.username)
            stream=io.BytesIO();Image.new('RGB',(50,50),'white').save(stream,'PNG')
            with patch('tracker.views.recognize',return_value=([],'Manuell korrigieren')):
                response=c.post(f'/verwaltung/student/{self.other.pk}/plan/',{'action':'upload','document':SimpleUploadedFile('plan.png',stream.getvalue())})
            self.assertEqual(response.status_code,302)
            p=Plan.objects.get();self.assertEqual(p.student,self.other)
            c.force_login(self.student);self.assertEqual(c.get(f'/plan/{p.pk}/dokument/').status_code,404)
    def test_admin_can_fill_and_submit_draft(self):
        p=Plan.objects.create(student=self.student)
        c=Client();c.force_login(self.admin)
        data={'form-TOTAL_FORMS':'1','form-INITIAL_FORMS':'0','form-0-start':'2026-04-01','form-0-end':'2026-04-30','form-0-phase':'practice','action':'submit'}
        for n in range(7):data[f'form-0-target_{n}']='8:00' if n<5 else '0:00';data[f'form-0-university_{n}']='0:00'
        response=c.post(f'/plan/{p.pk}/',data)
        self.assertEqual(response.status_code,302)
        p.refresh_from_db();self.assertEqual(p.status,'submitted')
        self.assertEqual(Audit.objects.get().kind,'plan_draft')
        self.assertEqual(c.get(f'/verwaltung/plan/{p.pk}/').status_code,200)
    def test_calendar_outline_is_bounded_and_unknown(self):
        text='Studienjahr 2025 2026 Okt.25 Nov.25 Dez.25 Jan.26 Feb.26 Mrz.26 Apr.26 Mai.26 Jun.26 Jul.26 Aug.26 Sep.26'
        s=calendar_outline(text)[0]
        self.assertEqual(s['start'],date(2025,10,1));self.assertEqual(s['end'],date(2026,9,30))
        self.assertEqual(s['phase'],'mixed');self.assertTrue(s['uncertainties'])
        self.assertTrue(all(d['target'] is None and d['university'] is None for d in s['week']))
    def test_calendar_without_anchored_full_year_is_not_guessed(self):
        self.assertEqual(calendar_outline('Okt.25 Nov.25 Dez.25 Jan.26 Feb.26'),[])

@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',DEFAULT_FROM_EMAIL='test@example.invalid')
class ReportConcurrency(Fixture,TransactionTestCase):
    def test_parallel_regular_delivery_is_once(self):
        r=prepare(date(2026,1,1),'test@example.invalid')
        def send(_):
            close_old_connections()
            try: return deliver(r.pk)
            finally: connections.close_all()
        with patch('tracker.reports.message') as mock:
            mock.return_value.send.return_value=1
            with ThreadPoolExecutor(max_workers=4) as executor:list(executor.map(send,range(4)))
            self.assertEqual(mock.return_value.send.call_count,1)
        r.refresh_from_db();self.assertEqual(r.status,'sent')

class AdditionalAcceptance(Fixture,TestCase):
    def test_stamp_resume_eight_hours(self):
        d=date(2026,4,13)
        for action,h,m in [('in',8,0),('pause',12,0),('resume',12,30),('out',16,30)]: stamp(self.student,action,uuid.uuid4(),at(d,h,m))
        self.assertEqual(day_value(self.student,d)['work'],8*3600)
    def test_same_key_for_another_action_is_not_replayed(self):
        key=uuid.uuid4();stamp(self.student,'in',key)
        with self.assertRaises(ValidationError):stamp(self.student,'pause',key)
        self.assertEqual(Pause.objects.count(),0)
    def test_second_overlapping_approved_leave_is_rejected(self):
        d=date(2026,4,13);self.plan(d,d)
        first=Leave.objects.create(student=self.student,start=d,end=d)
        second=Leave.objects.create(student=self.student,start=d,end=d)
        decide_leave(self.admin,first.pk,'approved','Geprüft')
        with self.assertRaises(ValidationError):decide_leave(self.admin,second.pk,'approved','Geprüft')
    def test_correction_forms_require_unambiguous_DST(self):
        from .forms import CorrectionForm
        data={'start':'25.10.2026 02:30','end':'25.10.2026 03:30','reason':'Test','expected':'x'}
        self.assertFalse(CorrectionForm(data).is_valid())
        data['start']='2026-10-25T02:30:00+02:00'
        form=CorrectionForm(data);self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual((form.cleaned_data['end'].astimezone(UTC.utc)-form.cleaned_data['start'].astimezone(UTC.utc)).total_seconds(),7200)
    def test_report_splits_midnight_at_month_boundary(self):
        Interval.objects.create(student=self.student,start=at(date(2026,1,31),23),end=at(date(2026,2,1),2))
        r=prepare(date(2026,1,1),'test@example.invalid')
        s=next(s for s in r.snapshot['students'] if s['id']==self.student.pk)
        self.assertEqual(s['sums']['work'],3600)
    def test_former_student_times_remain_in_report(self):
        Interval.objects.create(student=self.student,start=at(date(2026,1,12),8),end=at(date(2026,1,12),16))
        self.student.is_staff=True;self.student.save()
        r=prepare(date(2026,1,1),'test@example.invalid')
        s=next(s for s in r.snapshot['students'] if s['id']==self.student.pk)
        self.assertEqual(s['sums']['work'],8*3600)
    def test_report_counts_bookings_separately_from_affected_days(self):
        Interval.objects.create(student=self.student,start=at(date(2026,1,1),8))
        r=prepare(date(2026,1,1),'test@example.invalid')
        s=next(s for s in r.snapshot['students'] if s['id']==self.student.pk)
        self.assertEqual(len(s['open_bookings']),1)
        self.assertEqual(len(s['open_bookings'][0]['dates']),31)
        self.assertEqual(len(s['missing_plan_days']),31)

class PdfBoundaries(TestCase):
    def test_page_encryption_and_extension_limits(self):
        writer=PdfWriter()
        for _ in range(21):writer.add_blank_page(width=100,height=100)
        stream=io.BytesIO();writer.write(stream)
        with self.assertRaises(ValidationError):validate_document(SimpleUploadedFile('pages.pdf',stream.getvalue()))
        writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('artificial-test')
        stream=io.BytesIO();writer.write(stream)
        with self.assertRaises(ValidationError):validate_document(SimpleUploadedFile('locked.pdf',stream.getvalue()))
        image=Image.new('RGB',(50,50),'white');stream=io.BytesIO();image.save(stream,'PNG')
        with self.assertRaises(ValidationError):validate_document(SimpleUploadedFile('wrong.pdf',stream.getvalue()))

class HistoricalProtection(Fixture,TestCase):
    def test_global_rules_cannot_be_edited_after_confirmation(self):
        c=Client();c.force_login(self.admin)
        response=c.get('/admin/tracker/policy/1/change/')
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'name="state"')
        self.assertNotContains(response,'name="confirmed"')
        self.assertNotContains(response,'name="rules"')
    def test_plan_activation_requires_confirmed_business_rules(self):
        p=self.plan(date(2026,4,1),date(2026,4,3),active=False)
        _,token,_=plan_preview(p)
        Policy.objects.filter(pk=1).update(confirmed=False)
        with self.assertRaises(ValidationError):activate(self.admin,p.pk,token,'Freigabe')
    def test_recipient_change_does_not_replay_old_months(self):
        first=prepare(date(2026,1,1),'old@example.invalid')
        second=prepare(date(2026,1,1),'new@example.invalid')
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(second.recipient,'old@example.invalid')
        next_month=prepare(date(2026,2,1),'new@example.invalid')
        self.assertEqual(next_month.recipient,'new@example.invalid')
