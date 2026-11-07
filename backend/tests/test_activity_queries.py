"""Read pagination and search preserve existing activity and recipient scopes."""
from sqlalchemy import event as sql_event
from test_activities import client, create, find, money, login, post, expire, SessionLocal, employee_id
from app.activities_models import Activity
from app.v2_models import Employee
from app.database.connection import engine


def test_summary_pages_do_not_expand_prizes_or_recipient_details(client):
    event = create(client, publish=False)
    with SessionLocal() as db:
        original = db.get(Activity, event['id'])
        for number in range(13):
            db.add(Activity(owner_id=original.owner_id, title=f'分页测试{number}', kind=original.kind,
                description=original.description, state='draft', starts_at=original.starts_at,
                ends_at=original.ends_at, claim_until=original.claim_until, location=original.location,
                circles_json=original.circles_json, helpers_json='[]'))
        db.commit()
    statements=[]
    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement)
    sql_event.listen(engine, 'before_cursor_execute', capture)
    try:
        first = client.get('/api/activities?page_size=5&view=manage').json()
        second = client.get('/api/activities?page_size=5&page=2&view=manage').json()
    finally:
        sql_event.remove(engine, 'before_cursor_execute', capture)
    assert first['total'] >= 14
    assert len(first['items']) == len(second['items']) == 5
    assert not ({e['id'] for e in first['items']} & {e['id'] for e in second['items']})
    pages=[client.get('/api/activities',params={'page_size':5,'page':page,'view':'manage'}).json()
           for page in range(1,(first['total']+4)//5+1)]
    ids=[item['id'] for result in pages for item in result['items']]
    assert len(ids)==len(set(ids))==first['total']
    managed_ids=set()
    manage_page=1
    while True:
        result=client.get('/api/activities',params={'page_size':50,'view':'manage','page':manage_page}).json()
        managed_ids.update(e['id'] for e in result['items'])
        if manage_page*50>=result['total']:break
        manage_page+=1
    assert set(ids)==managed_ids
    assert all('lots' not in e and 'helper_ids' not in e for e in first['items'])
    assert not any('FROM activity_prizes' in statement for statement in statements)
    assert client.get('/api/activities?page=0').status_code == 422
    assert client.get('/api/activities?page_size=51').status_code == 422
    login(client, 'CMTEST02')
    assert event['id'] not in {e['id'] for e in client.get('/api/activities?page_size=50').json()['items']}
    assert client.get(f"/api/activities/{event['id']}").status_code == 404
    assert client.get('/api/activities?view=manage').json()['total'] == 0


def test_record_search_groups_pagination_and_recipient_privacy(client):
    event = create(client)
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/join', count=3).status_code == 200
    expire(lot_id)
    login(client, 'GSMTEST01')
    base = f"/api/activities/{event['id']}"
    detail = client.get(base).json()
    assert all(not lot['awards'] for lot in detail['lots'])
    assert detail['summary']['records'] == 3 and detail['summary']['ready'] == 3
    first = client.get(base+'/records?page_size=1').json()
    second = client.get(base+'/records?page_size=1&page=2').json()
    assert first['total'] == 3 and first['counts']['ready'] == 3
    assert first['items'][0]['id'] != second['items'][0]['id']
    complete=[client.get(base+'/records',params={'page_size':1,'page':page}).json()['items'][0]['id'] for page in range(1,4)]
    assert len(set(complete))==3
    award1, award2 = first['items'][0]['id'], second['items'][0]['id']
    assert post(client, f'awards/{award1}/fulfill', action='claim', reason='领取').status_code == 200
    assert post(client, f'awards/{award2}/fulfill', action='refund', reason='缺货').status_code == 200
    ready = client.get(base+'/records').json()
    assert ready['total'] == 1 and ready['counts'] == {'issued':1,'ready':1,'refunded':1}
    result = client.get(base+'/records', params={'status':'all','keyword':f'#{award2}'}).json()
    assert result['total'] == 1 and result['items'][0]['state'] == 'refunded'
    assert client.get(base+'/records',params={'status':'all','keyword':'CMTEST01'}).json()['total'] == 3
    assert client.get(base+'/records',params={'status':'all','keyword':'测试CM甲'}).json()['total'] == 3
    assert client.get(base+'/records',params={'status':'all','keyword':'%'}).json()['total'] == 0
    assert client.get(base+'/records',params={'status':'all','keyword':'9'*80}).status_code == 200
    assert client.get(base+'/records',params={'status':'all','keyword':'#'+'9'*79}).json()['total'] == 0
    assert client.get(base+'/records?status=invalid').status_code == 422
    login(client, 'CMTEST02')
    private = client.get(base+'/records?status=all').json()
    assert private['total'] == 0 and private['counts'] == {}
    login(client, 'CMTEST01')
    own = client.get(base+'/records?status=all').json()
    assert own['total'] == 3 and all('employee_no' not in a for a in own['items'])
    with SessionLocal() as db:
        employee = db.get(Employee, employee_id('CMTEST01'))
        previous = employee.attraction_id
        employee.attraction_id = None
        db.commit()
    try:
        assert event['id'] in {e['id'] for e in client.get('/api/activities?view=mine').json()['items']}
        assert client.get(base+'/records?status=all').json()['total'] == 3
        assert post(client, f'lots/{lot_id}/join', count=1).status_code in {404,409}
    finally:
        with SessionLocal() as db:
            db.get(Employee, employee_id('CMTEST01')).attraction_id = previous
            db.commit()


def test_counts_and_rows_share_a_snapshot_during_concurrent_claim(client):
    event = create(client)
    lot_id = event['lots'][0]['id']
    money('CMTEST01', 100)
    login(client, 'CMTEST01')
    assert post(client, f'lots/{lot_id}/join', count=3).status_code == 200
    expire(lot_id)
    login(client, 'GSMTEST01')
    base = f"/api/activities/{event['id']}"
    award_id = client.get(base+'/records').json()['items'][0]['id']
    changed=[]
    def concurrent_claim(conn, cursor, statement, params, context, many):
        if not changed and 'GROUP BY activity_awards.state' in statement:
            changed.append(True)
            with SessionLocal() as writer:
                from app.activities_models import ActivityAward
                writer.get(ActivityAward, award_id).state='claimed'
                writer.commit()
    sql_event.listen(engine,'after_cursor_execute',concurrent_claim)
    try:
        result=client.get(base+'/records').json()
    finally:
        sql_event.remove(engine,'after_cursor_execute',concurrent_claim)
    assert changed
    assert result['counts']['ready']==result['total']==len(result['items'])==3
    latest=client.get(base+'/records').json()
    assert latest['total']==2 and latest['counts']['claimed']==1
