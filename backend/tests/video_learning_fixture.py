import os,sys,json,subprocess,shutil
from pathlib import Path
root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'backend'))
work=Path(sys.argv[1]).resolve();work.mkdir(parents=True,exist_ok=True)
os.environ['RECOGNITION_V2_DATA_DIR']=str(work/'data')
os.environ['RECOGNITION_ENVIRONMENT']='test'
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS']='1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD']='fixture2026'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD']='fixture-admin2026'
from app.v2_database import init_db,SessionLocal
from app.v2_models import Employee
from app.video_models import LearningVideo,VideoAudiencePolicy
from app.video_worker import enqueue,run_one
from app import video_media as media
from datetime import datetime
init_db()
with SessionLocal() as db:
 if not db.query(LearningVideo).count():
  owner=db.query(Employee).filter_by(employee_no='SUPTEST01').one()
  titles=['开场前的设备与环境检查','游客沟通与现场协作','岗位交接中的关键确认']
  sample=work/'sample.mp4'
  subprocess.run([media.tool('ffmpeg'),'-nostdin','-y','-v','error','-f','lavfi','-i','testsrc2=size=960x540:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','4','-c:v','libx264','-threads','2','-pix_fmt','yuv420p','-c:a','aac',str(sample)],check=True)
  for title in titles:
   row=LearningVideo(owner_id=owner.id,attraction_id=owner.attraction_id,title=title,cover_title=title,description='隔离环境合成视频，仅用于验证上传、封面、个人水印、播放设置和权限。',original_bytes=sample.stat().st_size)
   db.add(row);db.flush();db.add(VideoAudiencePolicy(video_id=row.id,local_attraction_id=owner.attraction_id,codes=json.dumps(['all_tr','all_lead','all_gsm','am','om'])));directory=media.video_dir(row.id);directory.mkdir(parents=True,exist_ok=True);shutil.copyfile(sample,directory/'source.mov');enqueue(db,row.id,'source:'+str(row.id),'source')
  db.commit()
while run_one():pass
with SessionLocal() as db:
 for row in db.query(LearningVideo).all():
  if row.status=='ready':row.status='published';row.published_at=datetime.now()
 db.commit()
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')
