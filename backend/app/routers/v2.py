"""Aggregate router for the v2 API.

Endpoints live in per-domain modules; this module only mounts them under
/api so app.main and existing imports keep working unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    activities,
    activity_points,
    attendance_medals,
    scoring_config,
    about_system,
    accounts,
    announcement_media,
    announcements,
    auth,
    deductions,
    declaration_statistics,
    employees,
    export_tasks,
    files,
    governance,
    hr_admin,
    hr_monthly_reports,
    organization,
    recognitions,
    rotation_board,
    rotation_screen,
    sick_leaves,
    sick_leave_import,
    statistics,
    video_learning,
    learning,
)

from app.file_types import is_previewable_image, preview_kind
from app.warehouse.assessment import router as warehouse_assessment_router
from app.warehouse.core import router as warehouse_router


router = APIRouter(prefix="/api", tags=["v2"])

for _module in (
    activities,
    activity_points,
    attendance_medals,
    scoring_config,
    about_system,
    accounts,
    announcement_media,
    announcements,
    auth,
    deductions,
    declaration_statistics,
    employees,
    export_tasks,
    files,
    governance,
    hr_admin,
    hr_monthly_reports,
    organization,
    recognitions,
    rotation_board,
    rotation_screen,
    sick_leaves,
    sick_leave_import,
    statistics,
    video_learning,
    learning,
):
    router.include_router(_module.router)

router.include_router(warehouse_router)
router.include_router(warehouse_assessment_router)
