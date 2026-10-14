"""Integration check of local startup, restart, snapshot backup and restored data.
Creates only temporary synthetic data; runs on the current host, not a Windows claim.
"""
import os,sys,tempfile,subprocess,json,time,sqlite3,zipfile,urllib.request,urllib.error
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
SCRIPT=REPO/'portable'/'launcher.py'
process=None

def command(root,*args):
    env=dict(os.environ,CLOCKIN2_FOLDER=str(root),CLOCKIN2_NO_BROWSER='1')
    return subprocess.run([sys.executable,str(SCRIPT),*args],env=env,cwd=REPO,check=True,capture_output=True,text=True)
def shell(root,code):
    config=json.loads((root/'Daten'/'einstellungen.json').read_text())
    env=dict(os.environ,DJANGO_SECRET_KEY=config['secret_key'],DJANGO_SETTINGS_MODULE='config.portable_settings',PORTABLE_DATA_DIR=str(root/'Daten'))
    return subprocess.run([sys.executable,str(REPO/'manage.py'),'shell','-c',code],env=env,cwd=REPO,check=True,capture_output=True,text=True)
def start(root):
    global process
    env=dict(os.environ,CLOCKIN2_FOLDER=str(root),CLOCKIN2_NO_BROWSER='1')
    process=subprocess.Popen([sys.executable,str(SCRIPT),'start'],env=env,cwd=REPO,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    for _ in range(100):
        if process.poll() is not None:raise RuntimeError(process.stderr.read().decode())
        try:
            with urllib.request.urlopen('http://127.0.0.1:27173/anmeldung/',timeout=1) as r:
                text=r.read().decode()
                assert 'Anmelden' in text and 'csrfmiddlewaretoken' in text
            with urllib.request.urlopen('http://127.0.0.1:27173/static/app.css',timeout=1) as r:assert r.status==200
            return
        except (OSError,urllib.error.URLError):time.sleep(.1)
    raise RuntimeError('Lokaler Start nicht bereit')
def stop():
    global process
    if process:process.terminate();process.wait(timeout=15);process.stderr.close();process=None
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)/'Original';root.mkdir()
    command(root,'check')
    shell(root,"""
from django.contrib.auth import get_user_model
from tracker.models import Interval,Plan,Segment,Policy,Audit
from tracker.services import stamp
from datetime import date
from django.utils import timezone
from django.core.files.base import ContentFile
import uuid
u=get_user_model().objects.create_user('kuenstlicher_test')
stamp(u,'in',uuid.uuid4())
p=Plan.objects.create(student=u)
p.document.save('beispiel.txt',ContentFile(b'Kuenstliche Sicherungstestdatei'))
""")
    try:
        start(root);stop();start(root)
        shell(root,"""
from tracker.models import Interval
assert Interval.objects.filter(end=None).count()==1
""")
        command(root,'backup')
        stop()
        archive=sorted((root/'Sicherungen').glob('*.zip'))[-1]
        restored=Path(tmp)/'Wiederhergestellt';restored.mkdir()
        with zipfile.ZipFile(archive) as z:
            assert z.testzip() is None
            z.extractall(restored)
        with sqlite3.connect(restored/'Daten'/'zeiterfassung.sqlite3') as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert db.execute('SELECT count(*) FROM tracker_interval WHERE end IS NULL').fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM auth_user').fetchone()[0]==1
        assert next((restored/'Daten'/'Plaene').rglob('beispiel.txt')).read_bytes()==b'Kuenstliche Sicherungstestdatei'
        command(restored,'check');start(restored);stop()
        print('Ordnerprüfung bestanden: Einrichtung, lokales HTTP/CSS, Neustart, offene Buchung, laufende Sicherung, Daten- und Dateiwiederherstellung, Start nach Ordnerwechsel.')
    finally:stop()
