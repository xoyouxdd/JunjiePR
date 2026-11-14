"""Local browser fixture. Test-only routes are never mounted by production code."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_supervisor_performance import app, SessionLocal, employee_id
from test_activities import money, expire
from app.activities_models import Activity, ActivityAward, ActivityLot
from app.services.activities import movement, now
from datetime import timedelta


@app.on_event('startup')
def seed_activity_browser():
    money('CMTEST01', 45)
    money('CMTEST02', 100)


@app.post('/__fixture/end/{lot_id}')
def end_lot(lot_id: int):
    expire(lot_id)
    return {'ok': True}


@app.post('/__fixture/pending-redemptions')
def seed_pending_redemptions():
    """Additional real database rows for the isolated navigation preview."""
    with SessionLocal() as db:
        current = now()
        for title, cost, state in [
            ('秋日伙伴活动', 8, 'ready'),
            ('周末礼盒活动', 12, 'issued'),
            ('团队抽奖活动', 5, 'pending'),
        ]:
            event = Activity(owner_id=employee_id('GSMTEST01'), title=title, kind='draw', state='published',
                description='凭活动记录领取奖品。', location='员工服务台', starts_at=current-timedelta(hours=1),
                ends_at=current+timedelta(days=1), claim_until=current+timedelta(days=3))
            db.add(event)
            db.flush()
            lot = ActivityLot(activity_id=event.id, name='员工纪念礼盒', price=cost, ends_at=event.ends_at)
            db.add(lot)
            db.flush()
            award = ActivityAward(lot_id=lot.id, employee_id=employee_id('CMTEST01'), cost=cost, state=state)
            db.add(award)
            db.flush()
            movement(db, award, event, -cost, 'activity_spend')
        db.commit()
    return {'ok': True}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
