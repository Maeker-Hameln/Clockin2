import re,subprocess,tempfile,calendar
from pathlib import Path
from datetime import datetime
from PIL import Image,ImageOps
from pypdf import PdfReader
from django.conf import settings
from django.core.exceptions import ValidationError
MAX_BYTES=10*1024*1024
MAX_PAGES=20

def validate_document(upload):
    if upload.size>MAX_BYTES: raise ValidationError('Maximal 10 MB erlaubt.')
    head=upload.read(8);upload.seek(0)
    try:
        if head.startswith(b'%PDF-'):
            reader=PdfReader(upload)
            if reader.is_encrypted or not 0<len(reader.pages)<=MAX_PAGES: raise ValidationError('PDF muss unverschlüsselt sein und 1–20 Seiten enthalten.')
            kind='pdf'
        else:
            with Image.open(upload) as image:
                if image.format not in ['JPEG','PNG'] or image.width*image.height>25_000_000: raise ValidationError('Nur JPG/PNG bis 25 Megapixel erlaubt.')
                image.verify()
            kind='image'
    except ValidationError: raise
    except Exception: raise ValidationError('Datei ist kein gültiges PDF, JPG oder PNG.')
    finally: upload.seek(0)
    ext=Path(upload.name).suffix.lower()
    if (kind=='pdf' and ext!='.pdf') or (kind=='image' and ext not in ['.jpg','.jpeg','.png']): raise ValidationError('Dateiendung passt nicht zum erlaubten Dateityp.')
    return kind

def recognize(path):
    text=''
    try:
        if getattr(settings,'PORTABLE',False):
            from portable.ocr_local import read
            text=read(path)
        elif Path(path).suffix.lower()=='.pdf':
            reader=PdfReader(path)
            parts=[page.extract_text() or '' for page in reader.pages]
            # OCR only pages without usable text, including mixed text/scan PDFs.
            with tempfile.TemporaryDirectory() as tmp:
                for index,part in enumerate(parts,1):
                    if len(part.strip())>=30: continue
                    prefix=str(Path(tmp)/f'page{index}')
                    subprocess.run(['pdftoppm','-f',str(index),'-l',str(index),'-scale-to','2200','-singlefile','-png',path,prefix],check=True,timeout=45,capture_output=True)
                    parts[index-1]=ocr_image(prefix+'.png')
            text='\n'.join(parts)
        else: text=ocr_image(path)
    except Exception:
        return [],'Lokale Erkennung nicht möglich. Bitte Zeiträume und Stunden manuell erfassen.'
    suggestions=[]
    pattern=r'(\d{1,2}\.\d{1,2}\.\d{4})\s*(?:bis|[-–—])\s*(\d{1,2}\.\d{1,2}\.\d{4})'
    for line in text.splitlines():
        for match in re.finditer(pattern,line):
            try:
                start,end=[datetime.strptime(v,'%d.%m.%Y').date() for v in match.groups()]
                if end<start: continue
            except ValueError: continue
            line_lower=line.lower()
            phase='mixed' if 'gemischt' in line_lower else 'university' if any(w in line_lower for w in ['hochschule','theorie','studium']) else 'practice' if 'praxis' in line_lower else 'mixed'
            suggestions.append({'start':start,'end':end,'phase':phase,'week':[{'target':None,'university':None} for _ in range(7)],'uncertainties':'Stunden fehlen; Phasenerkennung und Zeiträume prüfen.'})
    if not suggestions:
        suggestions=calendar_outline(text)
    warning='Erkennung ist ein Vorschlag. Alle Zeiträume und Stunden prüfen.' if suggestions else 'Keine eindeutigen Zeiträume mit vollständigen Jahreszahlen erkannt. Bitte manuell erfassen.'
    return suggestions[:30],warning+'\n\n'+text[:30000]

def text_score(text):
    words=['praxis','semester','studium','hochschule','wochen','stunden','okt','nov','dez','jan','feb','mrz','apr','mai','jun','jul','aug','sep']
    return sum(text.lower().count(w) for w in words)+len(re.findall(r'\d{1,2}\.\d{1,2}\.\d{4}',text))*5

def raw_ocr(path):
    p=subprocess.run(['tesseract',str(path),'stdout','-l',settings.OCR_LANGUAGE]+(['--tessdata-dir',settings.OCR_TESSDATA_DIR] if settings.OCR_TESSDATA_DIR else []),check=True,timeout=60,capture_output=True,text=True)
    return p.stdout

def ocr_image(path):
    text=raw_ocr(path)
    with tempfile.TemporaryDirectory() as tmp:
        selected=Path(path)
        try:
            orientation=subprocess.run(['tesseract',str(path),'stdout','--psm','0','-l','osd'],timeout=15,capture_output=True,text=True)
            match=re.search(r'Rotate: (90|180|270)',orientation.stdout)
            angles=[int(match.group(1)),(int(match.group(1))+180)%360] if match else ([90,180,270] if text_score(text)<8 else [])
            for angle in angles:
                if not angle: continue
                rotated=Path(tmp)/f'upright-{angle}.png'
                with Image.open(path) as image: image.rotate(-angle,expand=True).save(rotated)
                candidate=raw_ocr(rotated)
                if text_score(candidate)>text_score(text): text,selected=candidate,rotated
        except (OSError,subprocess.SubprocessError): pass
        if any(word in text.lower() for word in ['semester','ablaufplan','exemplarisch']):
            # Small colored calendar headers benefit from a separate enlarged OCR pass.
            # This only supplies date labels, never hours or business rules.
            try:
                header=Path(tmp)/'header.png'
                with Image.open(selected) as image:
                    w,h=image.size
                    crop=image.crop((0,int(h*.10),int(w*.73),int(h*.21)))
                    crop=ImageOps.autocontrast(crop.convert('L'))
                    crop.resize((crop.width*3,crop.height*3)).save(header)
                result=subprocess.run(['tesseract',str(header),'stdout','-l',settings.OCR_LANGUAGE,'--psm','6']+(['--tessdata-dir',settings.OCR_TESSDATA_DIR] if settings.OCR_TESSDATA_DIR else []),check=True,timeout=30,capture_output=True,text=True)
                text+='\nKalenderkopf (lokale OCR):\n'+result.stdout
            except (OSError,subprocess.SubprocessError): pass
    return text

def calendar_outline(text):
    # Only bounded outlines from explicit month/year headers. No color-to-hours rules.
    months={'jan':1,'feb':2,'mrz':3,'mär':3,'apr':4,'mai':5,'jun':6,'jul':7,'aug':8,'sep':9,'okt':10,'nov':11,'dez':12}
    years={int(y) for y in re.findall(r'\b20\d{2}\b',text)}
    entries=set()
    for month,short in re.findall(r'\b(Jan|Feb|Mrz|Mär|Apr|Mai|Jun|Jul|Aug|Sep|Okt|Nov|Dez)\.?\s*(\d{2})\b',text,re.I):
        candidates=[y for y in years if y%100==int(short)]
        if len(candidates)==1: entries.add((candidates[0],months[month.lower()]))
    if len(entries)<4: return []
    ordered=sorted(entries)
    start=datetime(ordered[0][0],ordered[0][1],1).date()
    y,m=ordered[-1];end=datetime(y,m,calendar.monthrange(y,m)[1]).date()
    if (end-start).days>730: return []
    return [{'start':start,'end':end,'phase':'mixed','week':[{'target':None,'university':None} for _ in range(7)],'uncertainties':'Nur Gültigkeitsrahmen aus Monatsköpfen erkannt. Farbige Tagesphasen, Arbeitstage, Ausnahmen und Anrechnungen manuell prüfen und in passende Abschnitte aufteilen; keine Stunden übernommen.'}]
