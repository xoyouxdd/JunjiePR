"""Sanitized read-only backup health report projection."""
from __future__ import annotations

import json
import os
from pathlib import Path


BACKUP_HEALTH_STATUS_PATH = Path(
    os.environ.get(
        "RECOGNITION_BACKUP_HEALTH_STATUS_FILE",
        r"C:\Server\zhaojunjie\backups\recognition-card-system-sqlite\backup-health-status.json",
    )
)


def backup_health_payload() -> dict:
    """Read the fixed server health report without exposing paths or webhook data."""
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
    latest = raw.get("latest_backup") if isinstance(raw.get("latest_backup"), dict) else None
    alert = raw.get("alert") if isinstance(raw.get("alert"), dict) else {}
    issues = raw.get("issues") if isinstance(raw.get("issues"), list) else []
    return {
        "available": True,
        "ok": bool(raw.get("ok")),
        "status": "正常" if bool(raw.get("ok")) else "需处理",
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
