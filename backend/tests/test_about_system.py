"""Real sessions and isolated persistence for account-specific about-dialog edits."""
import json
import os
import tempfile
from datetime import date

import pytest

os.environ['RECOGNITION_V2_DATA_DIR'] = tempfile.mkdtemp(prefix='about-system-')
os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = '1234'
os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'HR123'

from fastapi.testclient import TestClient
from app.main import app
from app.v2_crypto import hash_password
from app.v2_database import SessionLocal, init_db
from app.v2_models import AboutSystemSettings, Attraction, AuditLog, Employee, EmployeeRoleAssignment, Role, UserAccount


@pytest.fixture(scope='module')
def client():
    with TestClient(app) as c:
        with SessionLocal() as db:
            circle = db.query(Attraction).filter_by(employee_circle=True, active=True).first()
            for login, role_code in [('ADMIN', 'SYSTEM_ADMIN'), ('OTHER-ADMIN', 'SYSTEM_ADMIN'),
                                     ('admin', 'SYSTEM_ADMIN'), ('ADMINX', 'SYSTEM_ADMIN'),
                                     ('ABOUT-HR', 'HR_ADMIN'), ('ABOUT-CIRCLE', 'HR_CIRCLE'),
                                     ('ABOUT-CM', 'CM'), ('ABOUT-GSM', 'GSM')]:
                employee = Employee(employee_no='EMP-' + login, name='ADMIN' if login == 'OTHER-ADMIN' else login,
                                    attraction_id=circle.id, is_active=True)
                db.add(employee)
                db.flush()
                role = db.query(Role).filter_by(code=role_code).one()
                db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=role.id,
                    starts_on=date.today().isoformat(), assignment_type='permanent', status='active'))
                db.add(UserAccount(employee_id=employee.id, login_account=login, enabled=True,
                    password_hash=hash_password('1234'), must_change_password=False, credential_initialized=True))
            hr = db.query(UserAccount).filter_by(login_account='HR01').one()
            hr.must_change_password = False
            db.commit()
        yield c


DEFAULT = '赵君杰、张森'


def login(client, account):
    client.post('/api/logout')
    response = client.post('/api/login', json={'employee_no': account, 'password': 'HR123' if account == 'HR01' else '1234'})
    assert response.status_code == 200, response.text


def test_default_is_read_only_and_does_not_write_on_get(client):
    login(client, 'HR01')
    assert client.get('/api/about-system').json() == {'maintainers': DEFAULT, 'feedback': '', 'revision': 0, 'can_edit': False}
    with SessionLocal() as db:
        assert db.get(AboutSystemSettings, 1) is None


@pytest.mark.parametrize('account', ['HR01', 'OTHER-ADMIN', 'admin', 'ADMINX', 'ABOUT-HR', 'ABOUT-CIRCLE', 'ABOUT-CM', 'ABOUT-GSM'])
def test_other_accounts_cannot_change_names_even_with_admin_role_or_name(client, account):
    login(client, account)
    before = client.get('/api/about-system').json()
    assert before['can_edit'] is False
    with SessionLocal() as db:
        count = db.query(AuditLog).filter_by(entity_type='about_system').count()
    result = client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '越权修改', 'revision': before['revision']})
    assert result.status_code == 403
    assert client.get('/api/about-system').json() == before
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(entity_type='about_system').count() == count


def test_admin_can_add_modify_remove_and_values_survive_startup(client):
    login(client, 'ADMIN')
    for value, expected in [(' 张森 \n 赵小明 ', '张森\n赵小明'), ('李晓', '李晓'), ('', ''), ('张森', '张森')]:
        before = client.get('/api/about-system').json()
        assert before['can_edit'] is True
        result = client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': value, 'revision': before['revision']})
        assert result.status_code == 200, result.text
        assert result.json() == {'maintainers': DEFAULT, 'feedback': expected, 'revision': before['revision'] + 1, 'can_edit': True}
        with SessionLocal() as db:
            log = db.query(AuditLog).filter_by(entity_type='about_system').order_by(AuditLog.id.desc()).first()
            assert json.loads(log.before_json) == {'maintainers': DEFAULT, 'feedback': before['feedback']}
            assert json.loads(log.after_json) == {'maintainers': DEFAULT, 'feedback': expected, 'login_account': 'ADMIN'}
            assert log.operator_id == db.query(UserAccount).filter_by(login_account='ADMIN').one().employee_id
    init_db()
    login(client, 'ABOUT-CM')
    assert client.get('/api/about-system').json()['feedback'] == '张森'


@pytest.mark.parametrize('body', [
    {'feedback': 'x' * 201, 'revision': 0}, {'feedback': None, 'revision': 0},
    {'feedback': ['张森'], 'revision': 0}, {'feedback': '\x00', 'revision': 0},
    {'feedback': '张森', 'revision': -1}, {'feedback': '张森', 'revision': True},
    {'feedback': '张森'}, {'feedback': '张森', 'revision': 0, 'maintainer': '其他人'},
    {'feedback': '张森', 'revision': 0, 'login_account': 'ADMIN'},
])
def test_invalid_or_extra_fields_cannot_change_settings(client, body):
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    assert client.put('/api/about-system', json={'maintainers': DEFAULT, **body}).status_code == 422
    assert client.get('/api/about-system').json() == before


def test_stale_write_is_rejected_without_audit_or_overwrite(client):
    login(client, 'ADMIN')
    old = client.get('/api/about-system').json()
    fresh = client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '新的名单', 'revision': old['revision']}).json()
    with SessionLocal() as db:
        count = db.query(AuditLog).filter_by(entity_type='about_system').count()
    for revision in [0, old['revision']]:
        assert client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '旧页面覆盖', 'revision': revision}).status_code == 409
    assert client.get('/api/about-system').json() == fresh
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(entity_type='about_system').count() == count


def test_disabled_admin_session_cannot_write(client):
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    with SessionLocal() as db:
        db.query(UserAccount).filter_by(login_account='ADMIN').one().enabled = False
        db.commit()
    try:
        assert client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '停用账号修改', 'revision': before['revision']}).status_code == 401
    finally:
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(login_account='ADMIN').one().enabled = True
            db.commit()


def test_employee_number_or_name_admin_does_not_grant_edit(client):
    login(client, 'OTHER-ADMIN')
    with SessionLocal() as db:
        employee = db.query(UserAccount).filter_by(login_account='OTHER-ADMIN').one().employee
        old_no = employee.employee_no
        employee.employee_no = 'ADMIN'
        db.commit()
    try:
        current = client.get('/api/about-system').json()
        assert current['can_edit'] is False
        assert client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '冒充', 'revision': current['revision']}).status_code == 403
    finally:
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(login_account='OTHER-ADMIN').one().employee.employee_no = old_no
            db.commit()


def test_unauthenticated_requests_are_rejected(client):
    client.post('/api/logout')
    assert client.get('/api/about-system').status_code == 401
    assert client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '匿名', 'revision': 0}).status_code == 401


def test_review_old_admin_editor_and_forged_identity_do_not_authorize_hr01(client):
    login(client, 'ADMIN')
    old_editor = client.get('/api/about-system').json()
    assert old_editor['can_edit'] is True
    login(client, 'HR01')
    response = client.put('/api/about-system?login_account=ADMIN&can_edit=true',
        headers={'X-Login-Account': 'ADMIN', 'X-Role': 'SYSTEM_ADMIN'},
        json={'maintainers': DEFAULT, 'feedback': '旧弹窗越权', 'revision': old_editor['revision']})
    assert response.status_code == 403
    current = client.get('/api/about-system').json()
    assert current == {**old_editor, 'can_edit': False}
    for method in ['POST', 'PATCH', 'DELETE']:
        assert client.request(method, '/api/about-system', json={'maintainers': DEFAULT, 'feedback': '绕过'}).status_code == 405


def test_review_live_account_name_is_rechecked_for_existing_session(client):
    login(client, 'ADMIN')
    old_editor = client.get('/api/about-system').json()
    with SessionLocal() as db:
        db.query(UserAccount).filter_by(login_account='ADMIN').one().login_account = 'RENAMED-ADMIN'
        db.commit()
    try:
        assert client.get('/api/about-system').json()['can_edit'] is False
        assert client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '旧身份越权', 'revision': old_editor['revision']}).status_code == 403
    finally:
        with SessionLocal() as db:
            db.query(UserAccount).filter_by(login_account='RENAMED-ADMIN').one().login_account = 'ADMIN'
            db.commit()


def test_review_expired_admin_session_cannot_write(client):
    from datetime import datetime, timedelta
    from app.v2_crypto import token_hash
    from app.v2_models import UserSession
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    with SessionLocal() as db:
        session = db.query(UserSession).filter_by(token_hash=token_hash(client.cookies.get('rc_v2_session'))).one()
        session.expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
    assert client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '过期会话', 'revision': before['revision']}).status_code == 401
    login(client, 'ABOUT-CM')
    assert client.get('/api/about-system').json() == {**before, 'can_edit': False}


def test_review_audit_failure_rolls_back_names_and_revision(client, monkeypatch):
    from app.routers import about_system
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    with SessionLocal() as db:
        count = db.query(AuditLog).filter_by(entity_type='about_system').count()

    def failed_audit(*args, **kwargs):
        raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(about_system, 'write_audit', failed_audit)
    with pytest.raises(RuntimeError, match='synthetic audit failure'):
        client.put('/api/about-system', json={'maintainers': DEFAULT, 'feedback': '应回滚名单', 'revision': before['revision']})
    assert client.get('/api/about-system').json() == before
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(entity_type='about_system').count() == count


def test_admin_edits_both_lists_atomically_and_restart_preserves_them(client):
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    response = client.put('/api/about-system', json={
        'maintainers': ' 赵君杰、张森 \n 新维护人 ', 'feedback': '建议人甲\n建议人乙', 'revision': before['revision']})
    assert response.status_code == 200, response.text
    expected = {'maintainers': '赵君杰、张森\n新维护人', 'feedback': '建议人甲\n建议人乙',
                'revision': before['revision'] + 1, 'can_edit': True}
    assert response.json() == expected
    init_db()
    assert client.get('/api/about-system').json() == expected
    login(client, 'HR01')
    assert client.get('/api/about-system').json() == {**expected, 'can_edit': False}
    assert 'contributors' not in expected


@pytest.mark.parametrize('maintainers', ['', ' \n ', 'x' * 201, None, ['维护人'], '\x00'])
def test_invalid_maintainer_rejects_both_list_changes(client, maintainers):
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    response = client.put('/api/about-system', json={
        'maintainers': maintainers, 'feedback': '不应保存', 'revision': before['revision']})
    assert response.status_code == 422
    assert client.get('/api/about-system').json() == before


def test_legacy_contributors_payload_cannot_overwrite_new_lists(client):
    login(client, 'ADMIN')
    before = client.get('/api/about-system').json()
    assert 'contributors' not in before
    response = client.put('/api/about-system', json={'contributors': '旧版名单', 'revision': before['revision']})
    assert response.status_code == 422
    assert client.get('/api/about-system').json() == before


def test_legacy_schema_upgrade_preserves_archive_and_does_not_reset_new_names():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.database.migrations.schema import ensure_about_system_credit_columns
    engine = create_engine('sqlite://')
    try:
        with Session(engine) as db:
            db.execute(text('CREATE TABLE about_system_settings (id INTEGER PRIMARY KEY, contributors TEXT NOT NULL, revision INTEGER NOT NULL)'))
            db.execute(text("INSERT INTO about_system_settings VALUES (1, '原共同参与人', 7)"))
            ensure_about_system_credit_columns(db)
            assert db.execute(text('SELECT contributors, maintainers, feedback, revision FROM about_system_settings')).one() == ('原共同参与人', DEFAULT, '', 7)
            db.execute(text("UPDATE about_system_settings SET maintainers='更新维护人', feedback='更新建议人', revision=8"))
            ensure_about_system_credit_columns(db)
            assert db.execute(text('SELECT contributors, maintainers, feedback, revision FROM about_system_settings')).one() == ('原共同参与人', '更新维护人', '更新建议人', 8)
    finally:
        engine.dispose()


def test_legacy_live_startup_and_save_preserve_other_modules(client):
    """Reconstruct the old table only inside this module's disposable database."""
    from sqlalchemy import text

    def other_data():
        with SessionLocal() as db:
            return {
                'accounts': db.query(UserAccount.id, UserAccount.employee_id, UserAccount.login_account,
                                     UserAccount.enabled, UserAccount.must_change_password).order_by(UserAccount.id).all(),
                'employees': db.query(Employee.id, Employee.employee_no, Employee.name,
                                      Employee.attraction_id, Employee.is_active).order_by(Employee.id).all(),
                'roles': db.query(Role.id, Role.code).order_by(Role.id).all(),
                'assignments': db.query(EmployeeRoleAssignment.id, EmployeeRoleAssignment.employee_id,
                                        EmployeeRoleAssignment.role_id, EmployeeRoleAssignment.starts_on,
                                        EmployeeRoleAssignment.ends_on, EmployeeRoleAssignment.status)
                                 .order_by(EmployeeRoleAssignment.id).all(),
            }

    before = other_data()
    with SessionLocal() as db:
        db.execute(text('DROP TABLE about_system_settings'))
        db.execute(text('CREATE TABLE about_system_settings (id INTEGER PRIMARY KEY CHECK (id=1), contributors TEXT NOT NULL, revision INTEGER NOT NULL)'))
        db.execute(text("INSERT INTO about_system_settings VALUES (1, '旧名单归档', 9)"))
        db.execute(text('DELETE FROM schema_migration_steps WHERE step=:step'),
                   {'step': '2026-11-about-system-credit-columns'})
        db.commit()
    init_db()
    assert other_data() == before
    login(client, 'HR01')
    migrated = client.get('/api/about-system').json()
    assert migrated == {'maintainers': DEFAULT, 'feedback': '', 'revision': 9, 'can_edit': False}
    change = {'maintainers': '赵君杰、张森、新维护人', 'feedback': '建议人甲', 'revision': 9}
    assert client.put('/api/about-system', json=change).status_code == 403
    assert client.get('/api/about-system').json() == migrated
    login(client, 'ADMIN')
    response = client.put('/api/about-system', json=change)
    assert response.status_code == 200, response.text
    assert response.json() == {**change, 'revision': 10, 'can_edit': True}
    init_db()
    assert client.get('/api/about-system').json() == response.json()
    assert other_data() == before
    with SessionLocal() as db:
        assert db.get(AboutSystemSettings, 1).contributors == '旧名单归档'
        assert db.execute(text('SELECT COUNT(*) FROM schema_migration_steps WHERE step=:step'),
                          {'step': '2026-11-about-system-credit-columns'}).scalar_one() == 1
