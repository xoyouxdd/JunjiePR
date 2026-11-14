from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
import json
from itertools import count
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import text

from test_supervisor_performance import app, SessionLocal, login, employee_id
from app.activity_points_models import ActivityPointsEntry, LegacyPointsBalance
from app.v2_models import Attraction, Employee, EmployeeRoleAssignment, ManagementScope, Role, UserAccount, AuditLog
from app.services import legacy_points as legacy
from app.services.legacy_points_xlsx import cell, tag

numbers = count(7100100)


@pytest.fixture
def system():
    with TestClient(app) as client:
        with SessionLocal() as db:
            circles = {c.name: c.id for c in db.query(Attraction).filter_by(employee_circle=True)}
            people = []
            for kind, archived in [('CM', False), ('SUPERVISOR', False), ('TR', True)]:
                e = Employee(employee_no=str(next(numbers)), name='历史'+kind, attraction_id=circles['热力追踪'],
                    hired_on='2025-01-01', is_active=not archived, terminated_on='2026-10-20' if archived else None)
                db.add(e);db.flush()
                db.add(EmployeeRoleAssignment(employee_id=e.id, role_id=db.query(Role).filter_by(code=kind).one().id,
                    starts_on='2025-01-01', assignment_type='permanent', status='active'))
                people.append(e.id)
            db.commit()
        login(client, 'GSMTEST01')
        yield client, circles, people


def export(client, circle, archived=False):
    response = client.post('/api/activity-points/legacy/export', params={'circle_id': circle, 'include_archived': archived})
    assert response.status_code == 200, response.text
    return response.content


def edit(content, edits):
    """User edits to disposable XLSX fixtures; all business templates use the authored base."""
    with ZipFile(BytesIO(content)) as z:
        files = {name: z.read(name) for name in z.namelist()}
    root = ET.fromstring(files['xl/worksheets/sheet1.xml'])
    for address, value in edits.items():
        number = ''.join(c for c in address if c.isdigit())
        row = root.find(f"{tag('sheetData')}/{tag('row')}[@r='{number}']")
        old = row.find(f"{tag('c')}[@r='{address}']")
        new = cell(address, value, old.attrib.get('s') if old is not None else None)
        if old is not None:
            row.remove(old)
        row.append(new)
    files['xl/worksheets/sheet1.xml'] = ET.tostring(root, encoding='utf-8')
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as z:
        for name, data in files.items():z.writestr(name, data)
    return output.getvalue()


def row_for(content, number):
    book = load_workbook(BytesIO(content), read_only=True)
    try:
        return next(row[0].row for row in book['名单'].iter_rows(min_row=4) if str(row[0].value) == str(number))
    finally:book.close()


def preview(client, circle, content):
    response = client.post('/api/activity-points/legacy/preview', params={'circle_id': circle}, files={'file': ('名单.xlsx', content)})
    assert response.status_code == 200, response.text
    return response.json()


def apply(client, result):
    return client.post('/api/activity-points/legacy/apply', json={'preview_id': result['preview_id']})


def number(eid):
    with SessionLocal() as db:return db.get(Employee, eid).employee_no


def test_formal_gsm_can_export_and_import_all_three_circles_without_scope_configuration(system):
    from urllib.parse import unquote

    client, circles, people = system
    names = ['热力追踪', '矮人迷宫', '小熊罐子']
    actor = employee_id('GSMTEST01')
    with SessionLocal() as db:
        lead = db.get(Employee, people[1])
        bear = db.get(Employee, people[2])
        lead.attraction_id = circles[names[1]]
        bear.attraction_id = circles[names[2]]
        bear.is_active, bear.terminated_on = True, None
        assert db.query(ManagementScope).filter(
            ManagementScope.employee_id == actor,
            ManagementScope.attraction_id.in_([circles[name] for name in names[1:]]),
        ).count() == 0
        db.commit()
    options = client.get('/api/activity-points/legacy/options').json()['circles']
    assert {c['name'] for c in options} == set(names)
    uploads = []
    for name, person, points in zip(names, people, [18, 12, 7]):
        circle = circles[name]
        response = client.post('/api/activity-points/legacy/export', params={'circle_id': circle})
        assert response.status_code == 200, response.text
        assert f'历史积点补录_{name}.xlsx' in unquote(response.headers['content-disposition'])
        book = load_workbook(BytesIO(response.content))
        sheet = book['名单']
        assert sheet['A1'].value == f'历史积点补录名单 · {name}'
        assert sheet['C3'].value == '所属景点圈'
        assert [sheet['H3'].value, sheet['I3'].value] == ['核定历史积点总数（填写）', '备注（修正必填）']
        row = row_for(response.content, number(person))
        assert sheet[f'H{row}'].fill.fgColor == sheet['H3'].fill.fgColor
        assert sheet[f'H{row}'].fill.fgColor != sheet[f'G{row}'].fill.fgColor
        assert all(r[2] == name for r in sheet.iter_rows(min_row=4, values_only=True) if r[0])
        book.close()
        uploads.append(edit(response.content, {f'H{row}': points}))

    for index, (name, content, points) in enumerate(zip(names, uploads, [18, 12, 7])):
        circle = circles[name]
        wrong_circle = circles[names[(index + 1) % len(names)]]
        rejected = client.post('/api/activity-points/legacy/preview', params={'circle_id': wrong_circle},
                               files={'file': ('名单.xlsx', content)})
        assert rejected.status_code == 409 and '不一致' in rejected.json()['detail']
        result = preview(client, circle, content)
        assert result['circle_name'] == name and result['summary']['increase'] == points
        assert apply(client, result).json()['changed'] == 1
        assert apply(client, result).json()['duplicate'] is True
        replay = preview(client, circle, content)
        assert not replay['errors'] and not replay['can_apply']
    with SessionLocal() as db:
        for person, points in zip(people, [18, 12, 7]):
            assert db.get(LegacyPointsBalance, person).total == points
            entries = db.query(ActivityPointsEntry).filter_by(employee_id=person, kind='legacy_import').all()
            assert [e.delta for e in entries] == [points]


def test_formal_gsm_only_and_scope(system):
    client, circles, people = system
    for account in ['CMTEST01', 'SUPTEST01', 'TAGSMTEST01', 'AMTEST01']:
        login(client, account)
        assert client.get('/api/activity-points/legacy/options').status_code == 403
        assert client.post('/api/activity-points/legacy/export', params={'circle_id': circles['热力追踪']}).status_code == 403
    client.post('/api/logout')
    assert client.post('/api/login', json={'employee_no': 'HR01', 'password': 'HR123'}).status_code == 200
    assert client.get('/api/activity-points/legacy/options').status_code == 403
    login(client, 'GSMTEST01')
    options = client.get('/api/activity-points/legacy/options').json()['circles']
    assert {circle['name'] for circle in options} == set(legacy.START_POLICY)
    assert client.post('/api/activity-points/legacy/export', params={'circle_id': 0}).status_code == 403


def test_roster_cutoffs_archived_and_export_round_trip(system):
    client, circles, people = system
    response = client.get('/api/activity-points/legacy/roster', params={'circle_id': circles['热力追踪']})
    assert response.status_code == 200, response.text
    rows = response.json()['items']
    rows = {r['employee_id']: r for r in rows}
    assert rows[people[0]]['cutoff'] == '2026-08'
    assert rows[people[1]]['cutoff'] == '2026-09'
    assert rows[people[2]]['status'] == '已归档'
    normal = export(client, circles['热力追踪'])
    with pytest.raises(StopIteration):row_for(normal, number(people[2]))
    full = export(client, circles['热力追踪'], True)
    assert row_for(full, number(people[2])) >= 4
    book = load_workbook(BytesIO(full), read_only=True)
    assert book['_control'].sheet_state == 'hidden'
    assert book['名单'].cell(row_for(full, number(people[0])), 1).data_type == 's'
    assert not preview(client, circles['热力追踪'], full)['errors']


def test_total_delta_repeat_correction_zero_and_personal_balance(system):
    client, circles, people = system
    content = export(client, circles['热力追踪'])
    row = row_for(content, number(people[0]))
    upload = edit(content, {f'H{row}': 18})
    first = preview(client, circles['热力追踪'], upload)
    assert first['can_apply'] and first['summary']['increase'] == 18
    assert apply(client, first).status_code == 200
    with SessionLocal() as db:
        from app.services.activities import balances
        assert balances(db, people[0])['earned'] == 18
        assert balances(db, people[0])['available'] == 18
    assert apply(client, first).json()['duplicate'] is True
    assert preview(client, circles['热力追踪'], upload)['summary']['increase'] == 0
    fresh = export(client, circles['热力追踪']);row = row_for(fresh, number(people[0]))
    invalid = preview(client, circles['热力追踪'], edit(fresh, {f'H{row}': 20}))
    assert invalid['errors'] and apply(client, invalid).status_code == 400
    second = preview(client, circles['热力追踪'], edit(fresh, {f'H{row}': 20, f'I{row}': '复核纸卡，补2点'}))
    assert second['summary']['increase'] == 2 and apply(client, second).status_code == 200
    stale = preview(client, circles['热力追踪'], upload)
    assert stale['errors']
    fresh = export(client, circles['热力追踪']);row = row_for(fresh, number(people[0]))
    zero = preview(client, circles['热力追踪'], edit(fresh, {f'H{row}': 0, f'I{row}': '核实原统计重复，核定为0'}))
    assert zero['summary']['decrease'] == 20 and apply(client, zero).status_code == 200
    with SessionLocal() as db:
        assert [e.delta for e in db.query(ActivityPointsEntry).filter_by(employee_id=people[0], kind='legacy_import').order_by(ActivityPointsEntry.id)] == [18, 2, -20]
        from app.services.activities import balances
        assert balances(db, people[0])['earned'] == 0


@pytest.mark.parametrize('bad', [-1, 1.5, '1e3', True, 1000001])
def test_invalid_scores_block_entire_batch(system, bad):
    client, circles, people = system
    content = export(client, circles['热力追踪'])
    a, b = row_for(content, number(people[0])), row_for(content, number(people[1]))
    upload = edit(content, {f'H{a}': 10, f'H{b}': bad})
    out = preview(client, circles['热力追踪'], upload)
    assert out['errors'] and not out['can_apply']
    assert apply(client, out).status_code == 400
    with SessionLocal() as db:assert db.get(LegacyPointsBalance, people[0]) is None


def test_readonly_tamper_duplicate_and_stale_preview(system):
    client, circles, people = system
    content = export(client, circles['热力追踪'])
    a, b = row_for(content, number(people[0])), row_for(content, number(people[1]))
    assert preview(client, circles['热力追踪'], edit(content, {f'B{a}': '冒名', f'H{a}': 18}))['errors']
    assert preview(client, circles['热力追踪'], edit(content, {f'A{b}': number(people[0])}))['errors']
    one = preview(client, circles['热力追踪'], edit(content, {f'H{a}': 18}))
    two = preview(client, circles['热力追踪'], edit(content, {f'H{a}': 20}))
    assert apply(client, one).status_code == 200
    assert apply(client, two).status_code == 409
    login(client, 'TAGSMTEST01')
    assert apply(client, one).status_code == 403


def test_mixed_history_exports_only_uncredited_intervals(system):
    client, circles, people = system
    with SessionLocal() as db:
        old = db.query(EmployeeRoleAssignment).filter_by(employee_id=people[0]).one()
        old.ends_on = '2026-09-14'
        db.add(EmployeeRoleAssignment(employee_id=people[0], role_id=db.query(Role).filter_by(code='SUPERVISOR').one().id,
            starts_on='2026-09-15', assignment_type='permanent', status='active'))
        db.commit()
    rows = client.get('/api/activity-points/legacy/roster', params={'circle_id': circles['热力追踪']}).json()['items']
    row = next(r for r in rows if r['employee_id'] == people[0])
    assert '2026-09-15至2026-09-30' in row['coverage'] and '2026-09-01至' not in row['coverage']
    assert row['needs_review']
    content = export(client, circles['热力追踪']);r = row_for(content, number(people[0]))
    assert preview(client, circles['热力追踪'], edit(content, {f'H{r}': 18}))['errors']


def test_concurrent_confirmations_and_expiration(system):
    client, circles, people = system
    content=export(client,circles['热力追踪']);r=row_for(content,number(people[0]))
    out=preview(client,circles['热力追踪'],edit(content,{f'H{r}':18}))
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:apply(client,out),range(2)))
    assert all(result.status_code==200 for result in results)
    assert sum(bool(result.json().get('duplicate')) for result in results)==1
    with SessionLocal() as db:
        assert db.query(ActivityPointsEntry).filter_by(employee_id=people[0],kind='legacy_import').count()==1
    fresh=export(client,circles['热力追踪']);r=row_for(fresh,number(people[0]))
    out=preview(client,circles['热力追踪'],edit(fresh,{f'H{r}':20,f'I{r}':'纸卡复核'}))
    from app.activity_points_models import LegacyPointsPreview
    from datetime import datetime,timedelta
    with SessionLocal() as db:
        db.get(LegacyPointsPreview,out['preview_id']).created_at=datetime.now()-timedelta(hours=1);db.commit()
    assert apply(client,out).status_code==409


def test_leading_zero_compatibility_and_formulas(system):
    client,circles,people=system
    content=export(client,circles['热力追踪']);r=row_for(content,number(people[0]))
    valid=preview(client,circles['热力追踪'],edit(content,{f'A{r}':'0'+number(people[0]),f'H{r}':18}))
    assert not valid['errors']
    assert preview(client,circles['热力追踪'],edit(content,{f'A{r}':'00'+number(people[0]),f'H{r}':18}))['errors']
    with ZipFile(BytesIO(edit(content,{f'H{r}':18}))) as z:files={name:z.read(name) for name in z.namelist()}
    root=ET.fromstring(files['xl/worksheets/sheet1.xml']);c=root.find(f"{tag('sheetData')}/{tag('row')}[@r='{r}']/{tag('c')}[@r='H{r}']")
    c.clear();c.set('r',f'H{r}');ET.SubElement(c,tag('f')).text='SUM(9,9)'
    files['xl/worksheets/sheet1.xml']=ET.tostring(root,encoding='utf-8');output=BytesIO()
    with ZipFile(output,'w',ZIP_DEFLATED) as z:
        for name,value in files.items():z.writestr(name,value)
    assert preview(client,circles['热力追踪'],output.getvalue())['errors']


def test_temporary_gsm_assignment_is_not_formal(system):
    client,circles,people=system
    with SessionLocal() as db:
        assignment=db.query(EmployeeRoleAssignment).filter_by(employee_id=employee_id('GSMTEST01')).one()
        assignment.assignment_type='temporary';db.commit()
    try:assert client.get('/api/activity-points/legacy/options').status_code==403
    finally:
        with SessionLocal() as db:
            db.query(EmployeeRoleAssignment).filter_by(employee_id=employee_id('GSMTEST01')).one().assignment_type='permanent';db.commit()


def test_archived_circle_can_be_restored_from_retained_employment(system):
    client,circles,people=system
    with SessionLocal() as db:
        employee=db.get(Employee,people[2]);employee.attraction_id=None
        employee.employment_history=json.dumps([{'hired_on':'2025-01-01','terminated_on':'2026-10-20','attraction_id':circles['热力追踪']}])
        db.commit()
    rows=client.get('/api/activity-points/legacy/roster',params={'circle_id':circles['热力追踪']}).json()['items']
    archived=next(r for r in rows if r['employee_id']==people[2])
    assert archived['status']=='已归档' and archived['cutoff']=='2026-08'


def test_cross_circle_history_is_partitioned_and_has_one_owner(system):
    client,circles,people=system
    from datetime import datetime
    from app.v2_models import CircleTransferRequest,ManagementScope
    actor=employee_id('GSMTEST01')
    with SessionLocal() as db:
        employee=db.get(Employee,people[0])
        db.add(CircleTransferRequest(employee_id=employee.id,employee_no=employee.employee_no,employee_name=employee.name,
            source_attraction_id=circles['矮人迷宫'],source_attraction_name='矮人迷宫',target_attraction_id=circles['热力追踪'],
            target_attraction_name='热力追踪',reason='历史调圈核对',status='completed',requested_by=actor,requested_by_name='GSM',
            completed_at=datetime(2026,9,15,10)))
        db.add(ManagementScope(employee_id=actor,attraction_id=circles['矮人迷宫'],starts_on='2026-01-01'))
        db.commit()
    try:
        rows=client.get('/api/activity-points/legacy/roster',params={'circle_id':circles['热力追踪']}).json()['items']
        row=next(r for r in rows if r['employee_id']==people[0])
        assert row['needs_review'] and '2026-09-14' in row['coverage']
        assert row['cutoff']=='2026-09'
        other=client.get('/api/activity-points/legacy/roster',params={'circle_id':circles['矮人迷宫']}).json()['items']
        assert all(r['employee_id']!=people[0] for r in other)
    finally:
        with SessionLocal() as db:
            db.query(ManagementScope).filter_by(employee_id=actor,attraction_id=circles['矮人迷宫']).delete();db.commit()


def test_all_import_endpoints_reject_nonformal_accounts(system):
    client, circles, people = system
    circle = circles['热力追踪']
    content = export(client, circle)
    row = row_for(content, number(people[0]))
    pending = preview(client, circle, edit(content, {f'H{row}': 18}))
    for account in ['CMTEST01', 'SUPTEST01', 'TAGSMTEST01', 'AMTEST01', 'HR01']:
        if account == 'HR01':
            client.post('/api/logout')
            assert client.post('/api/login', json={'employee_no': account, 'password': 'HR123'}).status_code == 200
        else:
            login(client, account)
        assert client.get('/api/activity-points/legacy/roster', params={'circle_id': circle}).status_code == 403
        assert client.post('/api/activity-points/legacy/preview', params={'circle_id': circle},
                           files={'file': ('名单.xlsx', content)}).status_code == 403
        assert apply(client, pending).status_code == 403
    with SessionLocal() as db:
        assert db.get(LegacyPointsBalance, people[0]) is None


def test_circle_deactivated_after_preview_blocks_confirmation(system):
    client, circles, people = system
    circle = circles['热力追踪']
    content = export(client, circle); row = row_for(content, number(people[0]))
    pending = preview(client, circle, edit(content, {f'H{row}': 18}))
    with SessionLocal() as db:
        db.get(Attraction, circle).active = False
        db.commit()
    try:
        assert apply(client, pending).status_code == 403
        assert client.post('/api/activity-points/legacy/preview', params={'circle_id': circle},
                           files={'file': ('名单.xlsx', content)}).status_code == 403
        with SessionLocal() as db:
            assert db.get(LegacyPointsBalance, people[0]) is None
    finally:
        with SessionLocal() as db:
            db.get(Attraction, circle).active = True
            db.commit()


def test_two_different_previews_confirm_concurrently_without_overwrite(system):
    client, circles, people = system
    circle = circles['热力追踪']
    content = export(client, circle); row = row_for(content, number(people[0]))
    pending = [preview(client, circle, edit(content, {f'H{row}': value})) for value in [18, 20]]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: apply(client, item), pending))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(item for item, result in zip(pending, results) if result.status_code == 200)
    total = winner['changes'][0]['total']
    with SessionLocal() as db:
        assert db.get(LegacyPointsBalance, people[0]).total == total
        entries = db.query(ActivityPointsEntry).filter_by(employee_id=people[0], kind='legacy_import').all()
        assert len(entries) == 1 and entries[0].delta == total


def test_old_file_and_preview_cannot_overwrite_after_total_returns_to_original(system):
    client, circles, people = system
    circle = circles['热力追踪']
    original = export(client, circle); row = row_for(original, number(people[0]))
    old_upload = edit(original, {f'H{row}': 25})
    old_preview = preview(client, circle, old_upload)
    for total in [18, 0]:
        fresh = export(client, circle); row = row_for(fresh, number(people[0]))
        pending = preview(client, circle, edit(fresh, {f'H{row}': total, f'I{row}': '重新核定历史总数'}))
        assert apply(client, pending).status_code == 200
    assert apply(client, old_preview).status_code == 409
    assert preview(client, circle, old_upload)['errors']
    with SessionLocal() as db:
        state = db.get(LegacyPointsBalance, people[0])
        assert state.total == 0 and state.revision == 2
        assert [e.delta for e in db.query(ActivityPointsEntry).filter_by(employee_id=people[0], kind='legacy_import').order_by(ActivityPointsEntry.id)] == [18, -18]


def test_confirmation_failure_rolls_back_whole_batch_and_allows_retry(system, monkeypatch):
    client, circles, people = system
    circle = circles['热力追踪']
    content = export(client, circle)
    a, b = [row_for(content, number(eid)) for eid in people[:2]]
    pending = preview(client, circle, edit(content, {f'H{a}': 18, f'H{b}': 20}))
    from app.activity_points_models import LegacyPointsPreview
    original_apply = legacy.apply
    def fail_after_flush(*args):
        original_apply(*args)
        raise RuntimeError('simulated failure before commit')
    with monkeypatch.context() as patch:
        patch.setattr(legacy, 'apply', fail_after_flush)
        with pytest.raises(RuntimeError, match='simulated failure'):
            apply(client, pending)
    with SessionLocal() as db:
        assert all(db.get(LegacyPointsBalance, eid) is None for eid in people[:2])
        assert db.query(ActivityPointsEntry).filter(ActivityPointsEntry.employee_id.in_(people[:2])).count() == 0
        assert db.get(LegacyPointsPreview, pending['preview_id']).applied_at is None
        assert db.query(AuditLog).filter_by(entity_type='legacy_points', entity_id=pending['preview_id']).count() == 0
    assert apply(client, pending).status_code == 200
    assert apply(client, pending).json()['duplicate'] is True


def test_correction_preserves_recognition_points_and_audit_history(system):
    client, circles, people = system
    circle = circles['热力追踪']
    with SessionLocal() as db:
        entry = ActivityPointsEntry(employee_id=people[0], recognition_id=None, revision=1, delta=7,
            kind='recognition', origin='historical_backfill', business_date='2026-10-01', detail_json='{}')
        db.add(entry); db.commit(); original_id = entry.id
    for total in [18, 15]:
        content = export(client, circle); row = row_for(content, number(people[0]))
        pending = preview(client, circle, edit(content, {f'H{row}': total, f'I{row}': '核对历史纸卡'}))
        assert apply(client, pending).status_code == 200
    with SessionLocal() as db:
        from app.services.activity_points import personal_card
        card = personal_card(db, people[0])
        assert card['earned'] == 22 and card['available'] == 22 and card['used'] == 0
        assert db.get(ActivityPointsEntry, original_id).delta == 7
        entries = db.query(ActivityPointsEntry).filter_by(employee_id=people[0], kind='legacy_import').order_by(ActivityPointsEntry.id).all()
        assert [e.delta for e in entries] == [18, -3]
        correction = json.loads(entries[-1].detail_json)
        assert correction['previous'] == 18 and correction['total'] == 15
        assert correction['note'] == '核对历史纸卡'
        assert db.query(AuditLog).filter_by(entity_type='legacy_points', entity_id=correction['preview_id']).count() == 1


def test_employee_transfer_after_preview_blocks_entire_batch(system):
    client, circles, people = system
    circle = circles['热力追踪']
    content = export(client, circle)
    a, b = [row_for(content, number(eid)) for eid in people[:2]]
    pending = preview(client, circle, edit(content, {f'H{a}': 18, f'H{b}': 20}))
    with SessionLocal() as db:
        db.get(Employee, people[1]).attraction_id = circles['矮人迷宫']; db.commit()
    assert apply(client, pending).status_code == 409
    with SessionLocal() as db:
        assert all(db.get(LegacyPointsBalance, eid) is None for eid in people[:2])
        assert db.query(ActivityPointsEntry).filter(ActivityPointsEntry.employee_id.in_(people[:2])).count() == 0
