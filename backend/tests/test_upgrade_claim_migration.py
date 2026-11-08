"""Legacy upgrade schema migration preserves history and restores FK enforcement."""
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

from test_deduction_upgrade_atomicity import upgrade_db
from app.database.migrations.upgrade_repair import ensure_rejected_upgrade_claim_release, _full_first_unique_indexes
from app.v2_models import DeductionUpgradeRequest, DeductionUpgradeTransfer, DeductionRecord, AuditLog


def legacy_schema(sessions,kind):
    if kind is None:return
    engine=sessions.kw['bind'];table=DeductionUpgradeRequest.__table__
    with engine.connect() as c:
        c.exec_driver_sql('PRAGMA foreign_keys=OFF');c.commit()
        c.exec_driver_sql('BEGIN IMMEDIATE')
        sql=str(CreateTable(table).compile(dialect=c.dialect)).strip().replace('CREATE TABLE deduction_upgrade_requests (','CREATE TABLE old_upgrade_shape (',1)
        if kind=='inline':sql=sql[:-1]+', UNIQUE(first_deduction_id))'
        c.exec_driver_sql(sql)
        cols=','.join(column.name for column in table.columns)
        c.exec_driver_sql(f'INSERT INTO old_upgrade_shape ({cols}) SELECT {cols} FROM deduction_upgrade_requests')
        c.exec_driver_sql('DROP TABLE deduction_upgrade_requests')
        c.exec_driver_sql('ALTER TABLE old_upgrade_shape RENAME TO deduction_upgrade_requests')
        for index in table.indexes:
            if index.name!='uq_deduction_upgrade_occupied_first' and not(kind=='index' and index.name=='ix_deduction_upgrade_requests_first_deduction_id'):
                index.create(bind=c)
        if kind=='index':c.exec_driver_sql('CREATE UNIQUE INDEX ix_deduction_upgrade_requests_first_deduction_id ON deduction_upgrade_requests(first_deduction_id)')
        c.commit();c.exec_driver_sql('PRAGMA foreign_keys=ON');c.commit()


def snapshot(db):
    return {name:[tuple(row) for row in db.execute(text(f'SELECT * FROM {name} ORDER BY id'))] for name in
            ('deduction_upgrade_requests','deduction_upgrade_transfers','deduction_records','stored_files','audit_logs')}


def prepare_history(sessions,ids,kind):
    legacy_schema(sessions,kind)
    with sessions() as db:
        work=db.get(DeductionUpgradeRequest,ids['request'])
        work.status='rejected';work.handling_note='历史审核不通过';work.resolved_by=ids['reviewer'];work.resolved_by_name='原经理'
        first=db.get(DeductionRecord,ids['first']);first.upgrade_request_id=None;first.upgrade_role=None;first.upgrade_state='eligible'
        second=db.get(DeductionRecord,ids['second']);second.status='void';second.upgrade_state='rejected';second.void_reason='历史审核不通过'
        db.add(DeductionUpgradeTransfer(request_id=ids['request'],from_reviewer_id=ids['reviewer'],from_reviewer_name='原经理',
            to_reviewer_id=ids['next_reviewer'],to_reviewer_name='接手经理',reason='历史交接原因'))
        db.add(AuditLog(operator_id=ids['reviewer'],operator_name='原经理',action='历史审计',entity_type='deduction_upgrade',entity_id=str(ids['request'])))
        db.execute(text('CREATE INDEX custom_upgrade_history_lookup ON deduction_upgrade_requests(handling_note)'))
        db.execute(text('CREATE TRIGGER custom_upgrade_history_trigger AFTER UPDATE OF handling_note ON deduction_upgrade_requests BEGIN SELECT 1; END'))
        db.commit();return snapshot(db)


@pytest.mark.parametrize('kind',[None,'inline','index'])
def test_upgrade_migration_preserves_all_history_and_is_idempotent(upgrade_db,kind):
    sessions,ids=upgrade_db;before=prepare_history(sessions,ids,kind)
    with sessions() as db:
        ensure_rejected_upgrade_claim_release(db)
        assert snapshot(db)==before
        ensure_rejected_upgrade_claim_release(db)
        assert snapshot(db)==before
        assert not _full_first_unique_indexes(db.connection())
        assert db.execute(text('PRAGMA foreign_keys')).scalar()==1
        assert list(db.execute(text('PRAGMA foreign_key_check')))==[]
        assert db.execute(text("SELECT count(*) FROM sqlite_master WHERE name IN ('custom_upgrade_history_lookup','custom_upgrade_history_trigger')")).scalar()==2
        # Preserve first IDs in rejected rows, while retaining the DB lock for
        # pending, conversion-waiting and approved work orders.
        original=db.get(DeductionUpgradeRequest,ids['request'])
        second=db.get(DeductionRecord,ids['second'])
        columns={col.name:getattr(second,col.name) for col in DeductionRecord.__table__.columns if col.name!='id'}
        columns['upgrade_request_id']=None
        extra=[DeductionRecord(**columns),DeductionRecord(**columns)];db.add_all(extra);db.flush()
        extra_ids=[r.id for r in extra];original.status='rejected';db.commit()
        def work(second_id,status):
            return DeductionUpgradeRequest(employee_id=original.employee_id,deduction_type_id=original.deduction_type_id,
                first_deduction_id=ids['first'],second_deduction_id=second_id,reviewer_id=ids['reviewer'],reviewer_name='经理',
                submitted_by=ids['reviewer'],submitted_by_name='经理',status=status)
        active=work(extra_ids[0],'awaiting_material');db.add(active);db.commit();active_id=active.id
        for status in ('awaiting_material','pending','approved'):
            db.get(DeductionUpgradeRequest,active_id).status=status;db.commit()
            db.add(work(extra_ids[1],'pending'))
            with pytest.raises(IntegrityError):db.commit()
            db.rollback()
        db.get(DeductionUpgradeRequest,active_id).status='rejected';db.commit()
        db.add(work(extra_ids[1],'pending'));db.commit()
        assert db.query(DeductionUpgradeRequest).filter_by(first_deduction_id=ids['first']).count()==3
        with pytest.raises(IntegrityError):
            db.execute(text('DELETE FROM deduction_records WHERE id=:id'),{'id':ids['first']});db.commit()
        db.rollback()


def test_failed_upgrade_rebuild_rolls_back_parent_children_and_fk_state(upgrade_db,monkeypatch):
    sessions,ids=upgrade_db;before=prepare_history(sessions,ids,'inline')
    execute=Connection.exec_driver_sql
    def fail_rename(connection,statement,*args,**kwargs):
        if statement.startswith('ALTER TABLE deduction_upgrade_requests_repair RENAME'):
            raise RuntimeError('injected upgrade migration failure')
        return execute(connection,statement,*args,**kwargs)
    with sessions() as db:
        with monkeypatch.context() as broken:
            broken.setattr(Connection,'exec_driver_sql',fail_rename)
            with pytest.raises(RuntimeError,match='injected'):
                ensure_rejected_upgrade_claim_release(db)
        assert snapshot(db)==before
        assert db.execute(text('PRAGMA foreign_keys')).scalar()==1
        assert _full_first_unique_indexes(db.connection())
        assert db.execute(text("SELECT count(*) FROM sqlite_master WHERE name='deduction_upgrade_requests_repair'")).scalar()==0
        ensure_rejected_upgrade_claim_release(db)
        assert snapshot(db)==before
