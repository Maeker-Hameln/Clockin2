"""Self-contained single-PC launcher; all mutable state belongs to Daten/."""
import argparse,getpass,json,os,secrets,socket,sqlite3,subprocess,sys,threading,time,uuid,webbrowser,zipfile
from datetime import date,datetime
from pathlib import Path
ROOT=Path(os.getenv('CLOCKIN2_FOLDER',str(Path(__file__).resolve().parents[2]))).resolve()
DATA=ROOT/'Daten'
_DLL_DIRS=[]

def local_folder():
    if os.name=='nt':
        import ctypes
        drive=str(ROOT.anchor)
        drive_type=ctypes.windll.kernel32.GetDriveTypeW
        drive_type.argtypes=[ctypes.c_wchar_p]
        drive_type.restype=ctypes.c_uint
        if drive.startswith('\\\\') or drive_type(drive)==4:
            raise RuntimeError('Bitte den Ordner auf die lokale Festplatte kopieren. Nicht direkt von M: oder einem Netzlaufwerk starten.')

def configure():
    local_folder()
    if os.name=='nt' and (ROOT/'Laufzeit').exists():
        _DLL_DIRS.append(os.add_dll_directory(str(ROOT/'Laufzeit')))
    DATA.mkdir(parents=True,exist_ok=True)
    file=DATA/'einstellungen.json'
    if not file.exists():
        config={'secret_key':secrets.token_urlsafe(64),'instance_id':str(uuid.uuid4()),'port':27173,'mail':{'EMAIL_HOST':'','EMAIL_PORT':'587','EMAIL_USE_TLS':'1','EMAIL_USE_SSL':'0','EMAIL_HOST_USER':'','EMAIL_HOST_PASSWORD':'','DEFAULT_FROM_EMAIL':''}}
        with file.open('x',encoding='utf-8') as f:json.dump(config,f,ensure_ascii=False,indent=2)
        try:file.chmod(0o600)
        except OSError:pass
    config=json.loads(file.read_text(encoding='utf-8'))
    os.environ['DJANGO_SECRET_KEY']=config['secret_key']
    os.environ['DJANGO_SETTINGS_MODULE']='config.portable_settings'
    os.environ['PORTABLE_DATA_DIR']=str(DATA)
    os.environ['PORTABLE_INSTANCE_ID']=config['instance_id']
    for name,value in config.get('mail',{}).items():os.environ[name]=str(value)
    os.environ['EMAIL_BACKEND']='django.core.mail.backends.smtp.EmailBackend'
    os.environ['DJANGO_DEBUG']='0'
    os.environ.pop('PYTHONHOME',None)
    return config

def setup():
    import django;django.setup()
    from django.core.management import call_command
    call_command('migrate',interactive=False,verbosity=0)
    call_command('collectstatic',interactive=False,verbosity=0)
    with sqlite3.connect(DATA/'zeiterfassung.sqlite3') as db:db.execute('PRAGMA journal_mode=WAL')

def first_account():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    users=get_user_model()
    if users.objects.exists():return
    print('\nErster Start: Verwaltungskonto anlegen. Keine voreingestellten Passwörter.\n')
    while True:
        name=input('Benutzername der Verwaltung [verwaltung]: ').strip() or 'verwaltung'
        try:
            users._meta.get_field('username').clean(name,None)
            user=users(username=name,is_staff=True,is_superuser=True)
            password=getpass.getpass('Passwort: ')
            if password!=getpass.getpass('Passwort wiederholen: '):print('Passwörter stimmen nicht überein.');continue
            validate_password(password,user)
            user.set_password(password);user.save();break
        except ValidationError as exc:print('; '.join(exc.messages))
    print('Konto angelegt. Im Browser unter Verwaltung ein Studentenkonto anlegen und einen Plan bestätigen.\n')

def backup():
    destination=ROOT/'Sicherungen';destination.mkdir(exist_ok=True)
    name=destination/(datetime.now().strftime('%Y-%m-%d_%H-%M-%S')+'_'+uuid.uuid4().hex[:6]+'.zip')
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        copy=Path(tmp)/'zeiterfassung.sqlite3'
        with sqlite3.connect(DATA/'zeiterfassung.sqlite3') as source,sqlite3.connect(copy) as target:source.backup(target)
        with zipfile.ZipFile(name,'x',zipfile.ZIP_DEFLATED) as archive:
            archive.write(copy,'Daten/zeiterfassung.sqlite3')
            for file in DATA.rglob('*'):
                if file.is_file() and file.name not in ['zeiterfassung.sqlite3','zeiterfassung.sqlite3-wal','zeiterfassung.sqlite3-shm']:
                    archive.write(file,'Daten/'+str(file.relative_to(DATA)).replace('\\','/'))
    print('Sicherung erstellt:',name.name)
    return name

def daily_backup():
    prefix=date.today().isoformat()
    if not any((ROOT/'Sicherungen').glob(prefix+'*.zip')):backup()

def monthly():
    from tracker.models import Policy
    from django.conf import settings
    policy=Policy.objects.filter(pk=1).first()
    if not policy or not policy.recipient or not policy.report_from or not settings.EMAIL_HOST or not settings.DEFAULT_FROM_EMAIL:
        return 'Monatsversand noch nicht eingerichtet; keine E-Mail versendet.'
    from tracker.reports import run_due
    return str(run_due())

def automate():
    if os.name!='nt':raise RuntimeError('Windows-Aufgabenplanung ist nur unter Windows verfügbar.')
    # Use the bundled interpreter, not an installed Python. Tasks run for this logged-in user.
    python=ROOT/'Laufzeit'/'python.exe'
    command=subprocess.list2cmdline([str(python),str(ROOT/'Programm'/'portable'/'launcher.py'),'job'])
    suffix=configure()['instance_id'][:8]
    tasks=[('Maeker-Zeiterfassung-'+suffix+'-taeglich',['/SC','DAILY','/ST','07:00']),('Maeker-Zeiterfassung-'+suffix+'-anmeldung',['/SC','ONLOGON'])]
    for name,schedule in tasks:
        subprocess.run(['schtasks','/Create','/TN',name,'/TR',command,*schedule,'/IT','/F'],check=True)
    print('Aufgaben eingerichtet: tägliche Sicherung und Prüfung fälliger Monatsberichte. Der PC muss eingeschaltet und der Benutzer angemeldet sein.')

def serve(config):
    from django.core.wsgi import get_wsgi_application
    from cheroot.wsgi import Server
    url=f"http://127.0.0.1:{int(config['port'])}/"
    import urllib.request
    try:
        with urllib.request.urlopen(url+'lokaler-status/',timeout=2) as response:
            status=json.load(response)
        if status.get('instance')==config['instance_id']:
            webbrowser.open(url);return
    except (OSError,ValueError):pass
    server=Server(('127.0.0.1',int(config['port'])),get_wsgi_application(),numthreads=4)
    try:server.prepare()
    except OSError:raise RuntimeError('Der lokale Port ist bereits belegt. Andere Kopie schließen oder port in Daten/einstellungen.json ändern.')
    def jobs():
        while True:
            try:daily_backup();monthly()
            except Exception as exc:
                with (DATA/'automatik.log').open('a',encoding='utf-8') as f:f.write(f'{datetime.now().isoformat()} {type(exc).__name__}: {exc}\n')
            time.sleep(60*60)
    threading.Thread(target=jobs,daemon=True).start()
    if not os.getenv('CLOCKIN2_NO_BROWSER'):webbrowser.open(url)
    print('Zeiterfassung läuft nur auf diesem PC. Dieses Fenster geöffnet lassen. Browser schließen beendet keine Buchung.\nZum Beenden hier Strg+C drücken.')
    try:server.serve()
    except KeyboardInterrupt:pass
    finally:server.stop()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',nargs='?',default='start',choices=['start','backup','job','automate','check'])
    args=parser.parse_args();config=configure()
    # File-backed SQLite IMMEDIATE transactions serialize writes across app/task processes.
    setup()
    if args.action=='check':
        from django.core.management import call_command
        call_command('check');print('Lokales Ordnerpaket geprüft.');return
    if args.action=='backup':backup();return
    if args.action=='job':daily_backup();print(monthly());return
    if args.action=='automate':automate();return
    first_account();daily_backup();serve(config)

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    try:main()
    except (Exception,KeyboardInterrupt) as exc:
        print('\nStart nicht abgeschlossen:',str(exc),file=sys.stderr)
        sys.exit(1)
