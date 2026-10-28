"""轮岗接口端到端：名单上传、草稿、发布、测试时钟、大屏操作、个人待办、记录与配置。"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import tempfile

from fastapi.testclient import TestClient
from openpyxl import Workbook


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-rotation-flow-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from app.main import app  # noqa: E402


DAY = "2026-10-04"
LINES = [
    {"id": "A", "group": "A", "posts": [{"name": "A入口"}, {"name": "迎宾", "seven": 1}, {"name": "A出口"}]},
    {"id": "B", "group": "B", "posts": [{"name": "B入口"}, {"name": "B中"}, {"name": "B出口"}]},
]
ROSTER = [
    ("9000001", "早班甲", "07:00-13:30", ""),
    ("9000002", "开园甲", "07:15-16:15", ""),
    ("9000003", "开园乙", "07:15-16:15", ""),
    ("9000004", "开园丙", "07:15-16:15", ""),
    ("9000005", "开园丁", "07:15-16:15", ""),
    ("9000006", "开园戊", "07:15-16:15", ""),
    ("9000007", "推七甲", "07:15-17:15", ""),
    ("9000008", "中班甲", "09:00-18:00", "1000-1100 SSEI"),
    ("9000009", "放休甲", "放休", ""),
]


def roster_file(day=DAY) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.append(["工号", "姓名", "日期", "班次", "备注"])
    for no, name, shift, note in ROSTER:
        ws.append([no, name, day, shift, note])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# The dedicated screen now uses formal data; sandbox flow checks use a manager
# and a personal sandbox session. Formal screen permissions/linkage are covered
# by test_rotation_formal.py, rather than assuming screen reads sandbox storage.

def ok(response):
    assert response.status_code==200,response.text
    return response.json()


def login(client,account,password='1234'):
    ok(client.post('/api/login',json={'employee_no':account,'password':password}))


def enter(client,account):ok(client.post('/api/rotation/enter',json={'account':account}))
def clock(client,**body):return client.post('/api/rotation/test-clock',json=body)
def person(board,pid):return next(p for p in board['day']['persons'] if p['pid']==pid)


def setup(manager,day,posts,people,time='07:14'):
    login(manager,'GSMTEST01');enter(manager,'7777777')
    ok(clock(manager,action='set',date=day,time='03:59'))
    ok(manager.put('/api/rotation/config',json={'lines':[{'id':'A','posts':posts}],
        'settings':{'breakMin':15,'parkClose':'22:00','closeDelay':0}}))
    book=Workbook();book.active.append(['工号','姓名','日期','班次','备注'])
    for pid,name,shift in people:book.active.append([pid,name,day,shift,''])
    stream=BytesIO();book.save(stream)
    ok(manager.post('/api/rotation/roster/upload',files={'file':('fixture.xlsx',stream.getvalue(),'application/octet-stream')},data={'scope':'day'}))
    ok(clock(manager,action='set',date=day,time=time))
    return ok(manager.get('/api/rotation/board'))


def test_prestart_unseated_seven_and_seven_fifteen_rest_timers_start_at_own_shift():
    day='2026-10-20'
    with TestClient(app) as manager,TestClient(app) as member:
        board=setup(manager,day,[{'name':'入口'}],[('9600100','初始甲','07:15-16:15'),('9600101','初始乙','07:15-16:15'),('9600102','早班','07:00-16:00')],time='06:59')
        waiting=[p for p in board['day']['persons'] if p['scheduledRestStart'] is not None]
        assert waiting and all(p['readyAt'] is None and p['state']=='notyet' for p in waiting)
        for p in waiting:
            login(member,'TRTEST01');enter(member,p['pid'])
            assert ok(member.get('/api/rotation/me'))['person']['scheduledRestStart']==p['start']
        ok(clock(manager,action='jump',minutes=16))
        for p in waiting:
            current=person(ok(manager.get('/api/rotation/board')),p['pid'])
            assert current['state']==('ready' if p['start']==420 else 'rest') and current['readyAt']==p['start']+15


def test_scheduled_opening_is_reserved_but_cannot_execute_before_allowed_time():
    day='2026-10-08';pid='9100001'
    with TestClient(app) as manager,TestClient(app) as member:
        setup(manager,day,[{'name':'迎宾','openAt':'08:15'}],[(pid,'加岗人员','07:15-16:15')],time='08:12')
        login(member,'TRTEST01');enter(member,pid)
        p=ok(member.get('/api/rotation/me'))['person']
        assert p['assign']['departAt']==495 and not p['canDepart']
        assert member.post('/api/rotation/me/act',json={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}).status_code==400
        ok(clock(manager,action='jump',minutes=3))
        p=ok(member.get('/api/rotation/me'))['person'];assert p['task']['allowedAt']==495 and p['task']['deadlineAt']==498
        result=ok(member.post('/api/rotation/me/act',json={'action':'depart','confirmation_token':p['confirmationTokens']['depart']}))
        assert result['result']['status']=='executed' and result['result']['post']=='迎宾'


def test_personal_confirmation_and_manager_share_receipt_without_impersonation():
    day='2026-10-11';pid='9300001'
    with TestClient(app) as manager,TestClient(app) as member:
        setup(manager,day,[{'name':'入口','openAt':'08:15'}],[(pid,'个人任务','07:15-16:15')],time='08:15')
        login(member,'TRTEST01');enter(member,pid)
        p=ok(member.get('/api/rotation/me'))['person'];body={'action':'depart','pid':pid,'confirmation_token':p['confirmationTokens']['depart']}
        assert member.post('/api/rotation/me/act',json={**body,'pid':'OTHER'}).status_code==403
        first=ok(member.post('/api/rotation/me/act',json=body));again=ok(manager.post('/api/rotation/act',json=body))
        assert again['duplicate'] and first['result']==again['result']
        assert ok(member.get('/api/rotation/confirmation',params={'date':day,'token':body['confirmation_token']}))['confirmed']
        assert person(ok(manager.get('/api/rotation/board')),pid)['state']=='onpost'
        ok(manager.post('/api/rotation/act',json={'action':'fix_remove','pid':pid,'reason':'测试到达确认'}))
        p=ok(member.get('/api/rotation/me'))['person'];room={'action':'arrive','pid':pid,'confirmation_token':p['confirmationTokens']['arrive']}
        arrived=ok(member.post('/api/rotation/me/act',json=room));repeated=ok(manager.post('/api/rotation/act',json=room))
        assert repeated['duplicate'] and arrived['result']['readyAt']==repeated['result']['readyAt']


def test_timeout_is_visible_and_person_must_acknowledge_before_new_task():
    day='2026-10-12';pid='9400001'
    with TestClient(app) as manager,TestClient(app) as member:
        setup(manager,day,[{'name':'入口','openAt':'08:15'}],[(pid,'超时人员','07:15-16:15')],time='08:15')
        login(member,'TRTEST01');enter(member,pid)
        p=ok(member.get('/api/rotation/me'))['person'];old=p['confirmationTokens']['depart']
        ok(clock(manager,action='jump',minutes=4))
        p=ok(member.get('/api/rotation/me'))['person'];assert p['entryHold'] and p['taskTimeout'] and p['assign'] is None
        assert member.post('/api/rotation/me/act',json={'action':'depart','confirmation_token':old}).status_code==409
        ok(member.post('/api/rotation/me/act',json={'action':'entry_return','confirmation_token':p['confirmationTokens']['entry_return']}))
        p=ok(member.get('/api/rotation/me'))['person'];assert not p['entryHold'] and p['taskTimeout']['acknowledged']


def test_sandbox_new_101_and_post_rename_use_stable_identity():
    day='2026-10-13'
    with TestClient(app) as manager:
        board=setup(manager,day,[{'name':'塔台'},{'name':'塔协'}],[('9500001','塔台人员','07:15-16:15'),('9500002','塔协人员','07:15-16:15')],time='07:15')
        key=board['day']['lines'][0]['posts'][0]['key']
        ok(manager.post('/api/rotation/act',json={'action':'rename_post','post_key':key,'name':'塔台改名'}))
        ok(manager.post('/api/rotation/act',json={'action':'hold101','on':True}))
        posts=ok(manager.get('/api/rotation/board'))['day']['lines'][0]['posts']
        assert all(p['hold101'] for p in posts) and posts[0]['key']==key
        ok(manager.post('/api/rotation/act',json={'action':'hold101','on':False}))
        assert not any(p['hold101'] for p in ok(manager.get('/api/rotation/board'))['day']['lines'][0]['posts'])


def test_daily_prearrangement_and_full_test_reset_preserve_original_plan():
    day='2026-10-14'
    with TestClient(app) as manager:
        board=setup(manager,day,[{'name':'入口'},{'name':'出口'}],[('9700001','预排甲','07:15-16:15'),('9700002','预排乙','07:15-16:15')],time='04:01')
        assert board['day']['status']=='live' and board['day']['taskWorkflow']==1
        before=[p['occ'] for p in board['day']['lines'][0]['posts']]
        reset=ok(clock(manager,action='reset'))
        assert reset['clock']['minute']==240
        after=ok(manager.get('/api/rotation/board'))
        assert [p['occ'] for p in after['day']['lines'][0]['posts']]==before
        assert all(p['continuousStart'] is None for p in after['day']['persons'])


def test_closed_day_has_no_pending_departures_and_stops_rotation():
    day='2026-10-15'
    with TestClient(app) as manager:
        setup(manager,day,[{'name':'入口'}],[('9800001','闭园人员','07:15-23:00')],time='07:15')
        ok(manager.post('/api/rotation/act',json={'action':'set_close','close':'07:16'}))
        ok(clock(manager,action='jump',minutes=2))
        board=ok(manager.get('/api/rotation/board'))
        assert board['day']['closed'] and not any(p['canDepart'] for p in board['day']['persons'])
