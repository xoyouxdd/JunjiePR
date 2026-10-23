"""Separate formal storage/runtime while sharing the scheduling rules with the sandbox."""
from types import SimpleNamespace
from threading import Lock
from sqlalchemy import Column, ForeignKey, Index, Table, UniqueConstraint
from app.database.connection import Base
from app.rotation import models

NAMES = ('RotationConfig', 'RotationRosterUpload', 'RotationRosterEntry', 'RotationDay',
         'RotationEvent', 'RotationSegment', 'RotationDuty', 'RotationNotice', 'RotationSnapshot')
TEST_TABLES = SimpleNamespace(**{name: getattr(models, name) for name in NAMES})
FORMAL_TABLES = SimpleNamespace()
targets = {getattr(models, name).__tablename__: 'formal_' + getattr(models, name).__tablename__ for name in NAMES}
for name in NAMES:
    source = getattr(models, name).__table__
    columns = []
    for col in source.columns:
        foreign_keys = []
        for fk in col.foreign_keys:
            table, column = fk.target_fullname.rsplit('.', 1)
            foreign_keys.append(ForeignKey(targets.get(table, table) + '.' + column, ondelete=fk.ondelete))
        columns.append(Column(col.name, col.type, *foreign_keys, primary_key=col.primary_key,
                              nullable=col.nullable, default=col.default, server_default=col.server_default))
    table = Table(targets[source.name], Base.metadata, *columns)
    for constraint in source.constraints:
        if isinstance(constraint, UniqueConstraint):
            table.append_constraint(UniqueConstraint(*[c.name for c in constraint.columns],
                                    name='formal_' + constraint.name if constraint.name else None))
    for index in source.indexes:
        Index('formal_' + index.name, *[table.c[c.name] for c in index.columns], unique=index.unique)
    setattr(FORMAL_TABLES, name, type('Formal' + name, (Base,), {'__table__': table, '__module__': __name__}))

_runtime = None
_lock = Lock()


def tables_for(db):
    return FORMAL_TABLES if db.info.get('rotation_formal') else TEST_TABLES


def runtime_for(db=None, *, formal=False):
    from app.rotation import service
    if not (formal or db is not None and db.info.get('rotation_formal')):
        return service.RUNTIME
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = service.Runtime()
    return _runtime


def runtime_for_row(row):
    return runtime_for(formal=row.__table__.name.startswith('formal_'))
