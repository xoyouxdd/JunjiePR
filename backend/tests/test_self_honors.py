"""Self-only honors on a disposable database; no production employee data."""
from decimal import Decimal

import pytest

from test_monthly_self_ranks import MONTH, close, system  # noqa: F401
from app.services.honors import editorial_order
from app.services.monthly_ranks import RANK_TIERS, sealed_self_score
from app.v2_models import AuditLog, EmployeeHonorMemory, EmployeeHonorStyle, RecognitionRecord, RecognitionType


def recognition(db, person, operator, circle, content, *, status="confirmed", credit=1, month=MONTH):
    kind = db.query(RecognitionType).first()
    row = RecognitionRecord(employee_id=person.id, employee_no=person.employee_no, employee_name=person.name,
        employee_role_snapshot="CM", home_attraction_id=circle.id, occurred_attraction_id=circle.id,
        recognition_date=month+"-08", recognition_month=month, recognition_type_id=kind.id,
        recognition_type_name=kind.name, content=content, recognizer_employee_id=operator.id,
        recognizer_name=operator.name, recognizer_role_snapshot="主管", operator_employee_id=person.id,
        operator_name=person.name, source="self", fraction=Decimal("1"), credited_fraction=Decimal(str(credit)), status=status)
    if credit == 0:
        row.monthly_cap_reason = "本月已达类别封顶"
    db.add(row)
    db.commit()
    return row


def test_latest_names_only_change_labels():
    assert [RANK_TIERS[key][0] for key in sorted(RANK_TIERS)] == [
        "启航新星", "成长青铜", "探索白银", "荣耀黄金", "流光铂金", "永恒钻石", "璀璨星耀", "超凡大师", "傲世宗师", "无双王者"]


def test_editorial_selection_keeps_originals_caps_and_self_scope(system):
    db, client, circles, _, make, switch, _, admin = system
    person, other = make("honor-a", points=12), make("honor-b", points=0)
    generic = recognition(db, person, admin, circles[0], "表现优秀，工作认真，值得表扬")
    concrete = recognition(db, person, admin, circles[0], "核对现场交接任务并完成复核", credit=0)
    for status in ("pending", "rejected", "void", "withdrawn"):
        recognition(db, person, admin, circles[0], "处理游客异常并完成复核", status=status)
    foreign = recognition(db, other, admin, circles[0], "帮助游客找回物品")
    previous = recognition(db, person, admin, circles[0], "核对现场交接事项", month="2026-09")
    switch(person)
    response = client.get('/api/self-honors', params={"month": MONTH, "employee_id": other.id})
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['vary'] == 'Cookie'
    data = response.json()
    assert [row['id'] for row in data['records']] == [concrete.id, generic.id]
    assert data['records'][0]['content'] == concrete.content
    assert data['records'][0]['cap_reason']
    assert data['letter'] is None and data['letter_state'] == 'waiting'
    assert client.put('/api/self-honors', params={"month": MONTH}, json={"favorite": True}).status_code == 409
    for record_id in (foreign.id, previous.id):
        assert client.put('/api/self-honors', params={"month": MONTH}, json={"selected_record_id": record_id}).status_code == 400
    for skin in ('sea', 'gold', 'moon', 'ice', 'jade', 'copper', 'forest', 'minimal'):
        saved = client.put('/api/self-honors', params={"month": MONTH}, json={"skin": skin})
        assert saved.status_code == 200, saved.text
        assert saved.json()['skin'] == skin  # Even pending/low/no medal can choose every skin.
    assert client.put('/api/self-honors', params={"month": MONTH}, json={"selected_record_id": generic.id}).status_code == 200
    assert client.get('/api/self-honors', params={"month": MONTH}).json()['selected_record_id'] == generic.id
    switch(other)
    own = client.get('/api/self-honors', params={"month": MONTH}).json()
    assert own['skin'] == 'sea' and own['selected_record_id'] == foreign.id
    assert db.query(EmployeeHonorStyle).count() == 1
    assert db.query(AuditLog).filter_by(action='honor_preference').count() == 9


def test_letter_sources_reopen_invalidation_and_removed_selection(system):
    db, client, circles, _, make, switch, holder, admin = system
    person = make('letter-person', points=12)
    peers = [make(f'letter-peer-{i}', points=i) for i in range(10)]
    rows = [recognition(db, person, admin, circles[0], text) for text in
            ("核对现场交接事项", "帮助游客确认路线", "完成设备点检复核", "认真负责")]
    close(client, circles[0].id)
    original = sealed_self_score(db, MONTH, person.id)
    before = (original.tier, original.total_score, original.comment_text, original.generation_id)
    switch(person)
    data = client.get('/api/self-honors', params={"month": MONTH}).json()
    assert data['letter_state'] == 'ready'
    letter = data['letter']
    assert len(letter['source_ids']) == 3
    by_id = {row.id: row for row in rows}
    for source_id in letter['source_ids']:
        assert f'“{by_id[source_id].content}”' in '\n'.join(letter['paragraphs'])
    assert not any(word in '\n'.join(letter['paragraphs']) for word in ('减少', '排名', '超过', '最优秀'))
    saved = client.put('/api/self-honors', params={"month": MONTH}, json={"selected_record_id": rows[0].id, "favorite": True}).json()
    assert saved['favorite'] and saved['selected_record_id'] == rows[0].id
    rows[0].status = 'void'
    db.commit()
    updated = client.get('/api/self-honors', params={"month": MONTH}).json()
    assert updated['selected_record_id'] != rows[0].id
    assert rows[0].id not in updated['letter']['source_ids']
    assert before == (original.tier, original.total_score, original.comment_text, original.generation_id)
    switch(admin, 'SYSTEM_ADMIN')
    holder['user'].permissions = {'SYSTEM_ADMIN'}
    reopened = client.post(f'/api/month-closes/{MONTH}/reopen', json={"attraction_id": circles[0].id, "reason": "验证回顾重开"})
    assert reopened.status_code == 200, reopened.text
    switch(person)
    assert client.get('/api/self-honors', params={"month": MONTH}).json()['letter'] is None
    assert db.query(EmployeeHonorMemory).count() == 1  # Preference survives reclose.


@pytest.mark.parametrize('body', [{"skin": "unknown"}, {"skin": None}, {"favorite": None}, {"favorite": "true"}, {"employee_id": 1}, {"selected_record_id": True}, {}])
def test_invalid_preferences_never_write(system, body):
    db, client, _, _, make, switch, _, _ = system
    switch(make('invalid-pref'))
    assert client.put('/api/self-honors', params={"month": MONTH}, json=body).status_code in (400, 422)
    assert db.query(EmployeeHonorMemory).count() == db.query(EmployeeHonorStyle).count() == 0


def test_permissions_future_feature_and_empty_month(system, monkeypatch):
    db, client, circles, _, make, switch, _, admin = system
    assert client.get('/api/self-honors', params={"month": MONTH}).status_code == 403
    switch(make('empty-honor', points=12))
    assert client.get('/api/self-honors', params={"month": '2099-01'}).status_code == 400
    monkeypatch.setenv('RECOGNITION_ENABLE_MONTHLY_RANKS', '0')
    assert client.get('/api/self-honors', params={"month": MONTH}).status_code == 404
    assert client.put('/api/self-honors', params={"month": MONTH}, json={"skin": "gold"}).status_code == 404
