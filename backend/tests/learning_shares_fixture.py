"""Only used by the isolated browser integration test, never touches local/production DB."""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['RECOGNITION_V2_DATA_DIR']=sys.argv[1]
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='fixture2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='fixture2026'
from app.main import app
from app.v2_database import init_db,SessionLocal,FILE_DIR
from app.v2_models import Employee,Attraction
from app.learning_models import LearningShare, LearningAsset, LearningAccess
from datetime import datetime,timedelta
from PIL import Image
Image.new('RGB',(160,80),'#1265d5').save(Path(sys.argv[1])/'sample.png')
import fitz
init_db()
with SessionLocal() as db:
    owner=db.query(Employee).filter_by(employee_no='TRTEST01').one()
    target=db.query(Attraction).filter(Attraction.employee_circle.is_(True),Attraction.active.is_(True),Attraction.id!=owner.attraction_id).first()
    path=FILE_DIR/'learning/browser.pdf';path.parent.mkdir(parents=True,exist_ok=True)
    with fitz.open() as pdf:
        pdf.new_page().insert_text((40,40),'Shared learning document');pdf.save(path)
    asset=LearningAsset(owner_id=owner.id,circle_id=target.id,filename='岗位攻略.pdf',key='learning/browser.pdf',media_type='application/pdf')
    db.add(asset);db.flush()
    db.add(LearningShare(circle_id=target.id,owner_id=owner.id,publisher_id=owner.id,title='跨圈学习材料',body='此内容需要组长批准后才能阅读。',assets='['+str(asset.id)+']'))
    db.commit()
@app.post('/__learning_test/expire')
def expire_for_browser_test():
    with SessionLocal() as db:
        db.query(LearningAccess).filter_by(status='approved').update({'expires_at':datetime.now()+timedelta(seconds=2)})
        db.commit()
    return {'ok':True}
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')
