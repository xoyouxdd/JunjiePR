"""Retain rejected work orders while releasing their first-statement claim."""
from sqlalchemy.schema import CreateTable


def _full_first_unique_indexes(connection):
    names = set()
    for index in connection.exec_driver_sql('PRAGMA index_list(deduction_upgrade_requests)'):
        name = str(index[1]).replace('"','""')
        if index[2] and not index[4]:
            columns = [row[2] for row in connection.exec_driver_sql(f'PRAGMA index_info("{name}")')]
            if columns == ['first_deduction_id']:
                names.add(index[1])
    return names


def ensure_rejected_upgrade_claim_release(db):
    """Transactional SQLite rebuild, with the same pinned FK-off connection.

    All work-order IDs and columns are copied verbatim. Child transfers, audit
    entries, statements and files are untouched. Unrejected work retains a DB
    unique claim, independently of the application's transactional row claim.
    """
    from app.v2_models import DeductionUpgradeRequest
    db.commit()
    table = DeductionUpgradeRequest.__table__
    with db.get_bind().connect() as connection:
        prior_fk = connection.exec_driver_sql('PRAGMA foreign_keys').scalar()
        connection.commit()
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        connection.commit()
        try:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            old_unique = _full_first_unique_indexes(connection)
            if old_unique:
                before_fk = {tuple(row) for row in connection.exec_driver_sql('PRAGMA foreign_key_check')}
                columns = [row[1] for row in connection.exec_driver_sql('PRAGMA table_info(deduction_upgrade_requests)')]
                if set(columns) != {column.name for column in table.columns}:
                    raise RuntimeError('升级工单表列与模型不一致，已停止迁移以保留原数据')
                indexes = [(row[0],row[1]) for row in connection.exec_driver_sql(
                    "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name='deduction_upgrade_requests' AND sql IS NOT NULL")]
                triggers = [row[0] for row in connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='deduction_upgrade_requests' AND sql IS NOT NULL")]
                sql = str(CreateTable(table).compile(dialect=connection.dialect)).strip()
                prefix = 'CREATE TABLE deduction_upgrade_requests ('
                if not sql.startswith(prefix):
                    raise RuntimeError('升级工单表结构无法安全迁移')
                connection.exec_driver_sql('CREATE TABLE deduction_upgrade_requests_repair ('+sql[len(prefix):])
                copied = ','.join('"'+name+'"' for name in columns)
                connection.exec_driver_sql(f'INSERT INTO deduction_upgrade_requests_repair ({copied}) SELECT {copied} FROM deduction_upgrade_requests')
                connection.exec_driver_sql('DROP TABLE deduction_upgrade_requests')
                connection.exec_driver_sql('ALTER TABLE deduction_upgrade_requests_repair RENAME TO deduction_upgrade_requests')
                for name, index_sql in indexes:
                    if name not in old_unique:
                        connection.exec_driver_sql(index_sql)
                for index in table.indexes:
                    index.create(bind=connection,checkfirst=True)
                for trigger in triggers:
                    connection.exec_driver_sql(trigger)
                after_fk = {tuple(row) for row in connection.exec_driver_sql('PRAGMA foreign_key_check')}
                if after_fk - before_fk:
                    raise RuntimeError('升级工单迁移外键核对失败，已回滚')
            else:
                for index in table.indexes:
                    index.create(bind=connection,checkfirst=True)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql(f'PRAGMA foreign_keys={int(bool(prior_fk))}')
            connection.commit()
