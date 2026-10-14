"""Offline OCR with models included in the installed wheel. No network requests."""
import calendar,tempfile
from pathlib import Path
from pypdf import PdfReader
from PIL import Image
import numpy as np
import onnxruntime
onnxruntime.disable_telemetry_events()
from rapidocr_onnxruntime import RapidOCR
from tracker.ocr import calendar_outline
_engine=None

def image_text(image):
    global _engine
    if _engine is None:_engine=RapidOCR()
    result,_=_engine(np.array(image.convert('RGB')))
    return '\n'.join(item[1] for item in (result or []))

def read(path):
    if Path(path).suffix.lower()=='.pdf':
        reader=PdfReader(path);parts=[]
        document=None
        try:
            for index,page in enumerate(reader.pages):
                text=page.extract_text() or ''
                if len(text.strip())<30:
                    import pypdfium2
                    if document is None:document=pypdfium2.PdfDocument(path)
                    rendered=document[index]
                    bitmap=rendered.render(scale=2)
                    try:text=image_text(bitmap.to_pil())
                    finally:bitmap.close();rendered.close()
                parts.append(text)
            return '\n'.join(parts)
        finally:
            if document is not None:document.close()
    with Image.open(path) as image:return image_text(image)
