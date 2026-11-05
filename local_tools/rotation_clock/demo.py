"""Synthetic identities and one imported-format roster, never copied from business data."""
from datetime import date
import json

from local_tools.rotation_clock.guard import require_local


def seed(path):
    require_local()
    if (path / '.demo-ready').exists():
        return
    from app.v2_database import init_db, SessionLocal
    from app.v2_models import Attraction, Employee, Role, EmployeeRoleAssignment, UserAccount
    from app.v2_crypto import hash_password
    from app.rotation import service as S
    from app.rotation.access import ensure_rotation_accounts
    init_db()
    with SessionLocal() as db:
        ensure_rotation_accounts(db)
        db.info['rotation_formal'] = True
        attraction = db.query(Attraction).filter_by(name='热力追踪').one()
        if S.day_row(db, attraction.id, '2026-11-05'):
            raise RuntimeError('已有轮岗数据但缺少演示初始化完成标识，拒绝覆盖')
        role = db.query(Role).filter_by(code='CM').one()
        people = {}
        shifts = {}
        for i in range(12):
            number = str(9720001 + i)
            employee = Employee(employee_no=number, name=f'模拟伙伴{i+1:02d}', attraction_id=attraction.id,
                                is_active=True, hired_on=date.today().isoformat())
            db.add(employee); db.flush()
            db.add(EmployeeRoleAssignment(employee_id=employee.id, role_id=role.id, starts_on=date.today().isoformat(),
                                           status='active', assignment_type='permanent', reason='本地轮岗模拟'))
            db.add(UserAccount(employee_id=employee.id, login_account=number,
                               password_hash=hash_password('rotation-local-2026'), enabled=True,
                               must_change_password=False, credential_initialized=True))
            people[number] = {'name': employee.name, 'type': '', 'mark': ''}
            shifts[number] = '07:15-16:15' if i < 8 else '16:00-00:30' if i < 10 else '19:00-02:00'
        db.commit()
        with S.transaction(db):
            cfg = S.load_config(db, attraction.id)
            cfg.lines_json = S._dumps([{'id': key, 'posts': [{'name': name} for name in names]}
                                      for key, names in [('A', ['上客一', '上协一', '下协一']), ('B', ['入口引导', '通道协助', '出口协助'])]])
            S.set_sim_clock(cfg, '2026-11-05', 405, speed=1, paused=True)
        S.save_roster(db, attraction.id, {'people': people, 'days': {'2026-11-05': shifts}, 'hours': {}},
                      scope='day', file_name='本地模拟班表.xlsx', uploader_id=None, uploader_name='本地模拟初始化')
        S.do_draft_action(db, attraction.id, 'local-demo', {'action': 'draft_generate', 'date': '2026-11-05'})
        S.do_draft_action(db, attraction.id, 'local-demo', {'action': 'publish', 'date': '2026-11-05'})
    (path / '.demo-ready').write_text(json.dumps({'date': '2026-11-05', 'synthetic': True}), encoding='utf-8')
