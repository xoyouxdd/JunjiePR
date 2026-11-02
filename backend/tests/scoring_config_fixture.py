"""Disposable API fixture for the scoring settings browser checks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from app.main import app
from app.v2_database import init_db, SessionLocal
from app.v2_models import UserAccount
from app.v2_crypto import hash_password

init_db()
with SessionLocal() as db:
    for row in db.query(UserAccount):
        row.must_change_password=False
        if row.login_account.startswith('HR-'):
            row.enabled=True
            row.password_hash=hash_password('1234')
    db.commit()

uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
