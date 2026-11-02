"""Sanitized read-only backup health report projection."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from app.runtime_environment import backup_health_path, validate_runtime_paths


BACKUP_HEALTH_STATUS_PATH = backup_health_path()
HEALTH_REPORT_MAX_AGE_HOURS = 26


def _age_hours(value, now: datetime) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return None
        age = (now - stamp.astimezone(timezone.utc)).total_seconds() / 3600
        return age if age >= -5 / 60 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _positive_hours(value, default):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value > 0 else default


def backup_health_payload() -> dict:
    """Read the fixed server health report without exposing paths or webhook data."""
    validate_runtime_paths(health_file=BACKUP_HEALTH_STATUS_PATH)
    default = {
        "available": False,
        "ok": False,
        "status": "未发现健康报告",
        "checked_at_utc": "",
        "task": {},
        "latest_backup": None,
        "issues": [{"code": "health_report_missing", "message": "未发现备份健康报告，请检查每日备份巡检任务。"}],
        "alert": {"configured": False, "attempted": False, "delivered": False, "last_alert_at_utc": ""},
    }
    try:
        raw = json.loads(BACKUP_HEALTH_STATUS_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError, TypeError):
        default["status"] = "健康报告无法读取"
        default["issues"] = [{"code": "health_report_invalid", "message": "备份健康报告无法读取，请检查巡检任务。"}]
        return default
    if not isinstance(raw, dict):
        default["status"] = "健康报告格式无效"
        default["issues"] = [{"code": "health_report_invalid", "message": "备份健康报告格式无效，请检查巡检任务。"}]
        return default
    task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
    latest = dict(raw["latest_backup"]) if isinstance(raw.get("latest_backup"), dict) else None
    alert = raw.get("alert") if isinstance(raw.get("alert"), dict) else {}
    issues = [dict(item) for item in raw.get("issues", []) if isinstance(item, dict)] if isinstance(raw.get("issues"), list) else []
    now = datetime.now(timezone.utc)
    report_age = _age_hours(raw.get('checked_at_utc'), now)
    def issue(code, message):
        if not any(item.get('code') == code for item in issues):
            issues.append({'code': code, 'message': message})
    if report_age is None:
        issue('health_report_time_invalid', '备份巡检报告时间无效，请检查巡检任务。')
    elif report_age > HEALTH_REPORT_MAX_AGE_HOURS:
        issue('health_report_stale', '备份巡检报告已超过26小时未更新，请检查巡检任务。')
    if latest is not None:
        age = _age_hours(latest.get('created_at_utc'), now)
        if not latest.get('created_at_utc') and report_age is not None:
            # Older reports may contain only the age measured at report creation.
            previous_age = latest.get('age_hours')
            if type(previous_age) in (int, float) and math.isfinite(previous_age) and previous_age >= 0:
                age = previous_age + max(0, report_age)
        latest['age_hours'] = round(max(0, age), 3) if age is not None else None
        thresholds = raw.get('thresholds') if isinstance(raw.get('thresholds'), dict) else {}
        maximum = _positive_hours(thresholds.get('max_backup_age_hours'), 26)
        if age is None:
            issue('backup_time_invalid', '备份文件时间无效，请核对最新备份。')
        elif age > maximum:
            issue('backup_stale', '最新备份已超过允许时限，请检查备份任务。')
    elif raw.get('ok'):
        issue('backup_missing', '巡检报告未包含可核实的最新备份。')
    ok = bool(raw.get('ok')) and not issues
    return {
        "available": True,
        "ok": ok,
        "status": "正常" if ok else "需处理",
        "checked_at_utc": str(raw.get("checked_at_utc") or ""),
        "task": {
            "exists": bool(task.get("exists")),
            "enabled": bool(task.get("enabled")),
            "state": str(task.get("state") or "未知"),
            "last_run_time_utc": str(task.get("last_run_time_utc") or ""),
            "next_run_time_utc": str(task.get("next_run_time_utc") or ""),
            "last_task_result": task.get("last_task_result"),
        },
        "latest_backup": None if latest is None else {
            "file_name": Path(str(latest.get("file_name") or "")).name,
            "created_at_utc": str(latest.get("created_at_utc") or ""),
            "age_hours": latest.get("age_hours"),
            "size_bytes": latest.get("size_bytes"),
            "manifest_verified": bool(latest.get("manifest_verified")),
            "sha256_verified": bool(latest.get("sha256_verified")),
            "size_verified": bool(latest.get("size_verified")),
            "quick_check": str(latest.get("quick_check") or ""),
        },
        "issues": [
            {"code": str(item.get("code") or "unknown"), "message": str(item.get("message") or "")}
            for item in issues if isinstance(item, dict)
        ][:20],
        "alert": {
            "configured": bool(alert.get("configured")),
            "attempted": bool(alert.get("attempted")),
            "delivered": bool(alert.get("delivered")),
            "last_alert_at_utc": str(alert.get("last_alert_at_utc") or ""),
        },
    }
