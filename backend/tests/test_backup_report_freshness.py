"""Health must age even when the scheduled report writer stops."""
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ['RECOGNITION_V2_DATA_DIR']=tempfile.mkdtemp(prefix='recognition-health-freshness-')

import pytest
from app import operations_health as H


@pytest.fixture
def report(tmp_path,monkeypatch):
    path=tmp_path/'health.json'
    monkeypatch.setenv('RECOGNITION_V2_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(H,'BACKUP_HEALTH_STATUS_PATH',path)
    now=datetime.now(timezone.utc)
    data={'ok':True,'checked_at_utc':now.isoformat(),'issues':[],
          'latest_backup':{'created_at_utc':(now-timedelta(hours=1)).isoformat(),'age_hours':1},
          'thresholds':{'max_backup_age_hours':26}}
    def read(**changes):
        path.write_text(json.dumps({**data,**changes}),encoding='utf-8')
        return H.backup_health_payload()
    return now,data,read


def test_recent_success_stays_healthy_and_recomputes_backup_age(report):
    now,data,read=report
    data['latest_backup']['age_hours']=100
    result=read()
    assert result['ok'] and result['issues']==[]
    assert 1<=result['latest_backup']['age_hours']<1.1


@pytest.mark.parametrize('stamp',[None,'bad','2026-01-01', '2100-01-01T00:00:00Z'])
def test_invalid_or_future_report_never_claims_success(report,stamp):
    _,_,read=report
    result=read(checked_at_utc=stamp)
    assert not result['ok']
    assert 'health_report_time_invalid' in {r['code'] for r in result['issues']}


def test_stopped_report_writer_turns_previous_success_into_alert(report):
    now,data,read=report
    result=read(checked_at_utc=(now-timedelta(hours=27)).isoformat())
    assert not result['ok']
    assert 'health_report_stale' in {r['code'] for r in result['issues']}


def test_fresh_report_cannot_hide_old_backup_and_honors_configured_threshold(report):
    now,data,read=report
    data['latest_backup']['created_at_utc']=(now-timedelta(hours=30)).isoformat()
    assert not read()['ok']
    assert read(thresholds={'max_backup_age_hours':48})['ok']


def test_legacy_age_grows_with_report_age_and_alerts_are_not_duplicated(report):
    now,data,read=report
    result=read(checked_at_utc=(now-timedelta(hours=2)).isoformat(),latest_backup={'age_hours':25},
                issues=[{'code':'backup_stale','message':'existing'}],ok=False)
    assert not result['ok'] and result['latest_backup']['age_hours']>=27
    assert result['issues']==[{'code':'backup_stale','message':'existing'}]
