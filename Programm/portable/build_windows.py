"""Build a relocatable directory; inputs are verified Python/wheel downloads."""
import base64,csv,hashlib,io,json,shutil,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TARGET=Path('/workspace/Maeker-Zeiterfassung-Windows')
RUNTIME=Path('/workspace/clockin2-windows-runtime/cpython-3.12.15-windows-x86_64-none')
WHEELS=Path('/workspace/clockin2-windows-wheels')
# Rebuild only the code/runtime folders; preserve any existing user data.
TARGET.mkdir(exist_ok=True)
for part in ['Programm','Laufzeit']:
    dest=TARGET/part
    if dest.exists():shutil.rmtree(dest)
program=TARGET/'Programm';program.mkdir()
for name in ['config','tracker','templates','static','portable']:
    shutil.copytree(ROOT/name,program/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copy2(ROOT/'manage.py',program/'manage.py')
shutil.copytree(RUNTIME,TARGET/'Laufzeit',ignore_dangling_symlinks=True)
site=TARGET/'Laufzeit'/'Lib'/'site-packages';site.mkdir(exist_ok=True)
manifest=[]
for wheel in sorted(WHEELS.glob('*.whl')):
    # Verify every wheel member against RECORD before extraction.
    with zipfile.ZipFile(wheel) as z:
        record=next(n for n in z.namelist() if n.endswith('.dist-info/RECORD'))
        for name,digest,size in csv.reader(io.StringIO(z.read(record).decode())):
            if digest:
                algorithm,expected=digest.split('=',1)
                actual=base64.urlsafe_b64encode(hashlib.new(algorithm,z.read(name)).digest()).decode().rstrip('=')
                if actual!=expected:raise RuntimeError('Wheel-Prüfsumme stimmt nicht: '+wheel.name+' '+name)
        for info in z.infolist():
            if info.is_dir():continue
            parts=Path(info.filename).parts
            if '..' in parts or Path(info.filename).is_absolute():raise RuntimeError('Ungültiger Wheel-Pfad')
            if len(parts)>2 and parts[0].endswith('.data'):
                scheme=parts[1]
                base=site if scheme in ['purelib','platlib'] else TARGET/'Laufzeit'
                if scheme=='scripts':base=TARGET/'Laufzeit'/'Scripts'
                dest=base.joinpath(*parts[2:])
            else:dest=site/info.filename
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(info))
    manifest.append({'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()})
# Keep the verified redistributable CRT together next to python.exe.
for file in (TARGET/'Laufzeit').rglob('*.dll'):
    if file.parent==TARGET/'Laufzeit'/'Scripts' and file.name.lower().startswith(('msvcp','vcruntime','concrt','vcomp')):
        shutil.copy2(file,TARGET/'Laufzeit'/file.name)
shutil.copy2(ROOT/'portable'/'ANLEITUNG.txt',TARGET/'BITTE_ZUERST_LESEN.txt')
commands={'START.cmd':'start','SICHERN.cmd':'backup','MONATSBERICHT.cmd':'job','AUTOMATIK_EINRICHTEN.cmd':'automate'}
for name,action in commands.items():
    script='@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n"%~dp0Laufzeit\\python.exe" "%~dp0Programm\\portable\\launcher.py" '+action+'\r\nif errorlevel 1 echo Vorgang nicht abgeschlossen. Bitte Fehlermeldung oben beachten.\r\npause\r\n'
    (TARGET/name).write_bytes(script.encode('ascii'))
(TARGET/'PAKET_INHALT.json').write_text(json.dumps({'python':'CPython 3.12.15 Windows x64 (Astral python-build-standalone via uv, with upstream verification)','wheels':manifest},indent=2),encoding='utf-8')
(TARGET/'DRITTANBIETER.txt').write_text('Python und alle Bibliotheken sind mit ihren Lizenzdateien und dist-info-Metadaten unter Laufzeit enthalten. OCR-Modelle liegen lokal im rapidocr_onnxruntime-Paket. Microsoft-CRT-DLLs sind aus dem msvc-runtime-Wheel enthalten. Komponenten und Download-Prüfsummen: PAKET_INHALT.json.\n',encoding='utf-8')
archive=Path('/workspace/Maeker-Zeiterfassung-Windows.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for file in TARGET.rglob('*'):
        if file.is_file() and file.relative_to(TARGET).parts[0] not in ['Daten','Sicherungen']:
            z.write(file,'Maeker-Zeiterfassung/'+str(file.relative_to(TARGET)))
with zipfile.ZipFile(archive) as z:
    if z.testzip() is not None:raise RuntimeError('Paketintegrität fehlerhaft')
print(f'Windows-Ordnerpaket erstellt: {archive.stat().st_size/1024/1024:.1f} MB')
