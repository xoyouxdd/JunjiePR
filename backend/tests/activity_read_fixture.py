"""Isolated, internally consistent data for list and receipt UI review."""
import sys
from pathlib import Path
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_supervisor_performance import app, SessionLocal, employee_id
from test_activities import money
from app.activities_models import Activity, ActivityLot, ActivityPrize, ActivityAward
from app.services import activities as service


@app.on_event('startup')
def seed():
    money('CMTEST01',1000)
    money('CMTEST02',1000)
    with SessionLocal() as db:
        now=service.now()
        for number in range(14):
            event=Activity(owner_id=employee_id('GSMTEST01'),title='秋日伙伴礼遇' if number==13 else f'伙伴活动 {number+1:02d}',
                kind='draw',description='用日常认可积点，兑换属于伙伴的小惊喜。',state='published',
                starts_at=now-timedelta(days=1),ends_at=now-timedelta(minutes=10),
                claim_until=now+timedelta(days=5),location='团队休息区 · 请携带领取凭证',circles_json='[]',helpers_json='[]')
            db.add(event);db.flush()
            if number!=13:
                continue
            lot=ActivityLot(activity_id=event.id,name='20点心意奖池',price=20,per_person=100,
                step=1,extend_seconds=0,ends_at=event.ends_at,state='settled')
            db.add(lot);db.flush()
            prize=ActivityPrize(lot_id=lot.id,name='秋日纪念礼盒',description='伙伴专属心意',quantity=27)
            db.add(prize);db.flush()
            for index in range(27):
                state='ready' if index<23 else 'claimed' if index<26 else 'refunded'
                award=ActivityAward(lot_id=lot.id,employee_id=employee_id('CMTEST01' if index%2==0 else 'CMTEST02'),
                    cost=20,prize_id=prize.id,state=state,reason='缺货退回原积点' if state=='refunded' else '')
                db.add(award);db.flush();service.movement(db,award,event,-20,'activity_spend')
                if state=='refunded':service.movement(db,award,event,20,'activity_refund')
        db.commit()


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
