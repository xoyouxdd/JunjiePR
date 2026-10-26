"""Real upload UI fixture with isolated data and file-byte readback."""
import sys
import hashlib
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_supervisor_performance import app, SessionLocal
from app.v2_database import FILE_DIR
from app.v2_models import RecognitionRecord
from fastapi.responses import Response
from PIL import Image


@app.get('/__fixture/photo/{color}')
def photo(color: str):
    out=BytesIO();Image.new('RGB',(320,200), 'blue' if color=='blue' else 'red').save(out,format='PNG')
    return Response(out.getvalue(),media_type='image/png')


@app.get('/__fixture/receipt/{record_id}')
def receipt(record_id: int):
    with SessionLocal() as db:
        row=db.get(RecognitionRecord,record_id)
        return [{'name':a.file.original_filename,'sha256':hashlib.sha256((FILE_DIR/a.file.storage_key).read_bytes()).hexdigest(),
                 'file_id':a.file_id,'size':a.file.file_size} for a in row.attachments]


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
