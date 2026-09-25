"""Aggregate router for the v2 API.

Endpoints live in per-domain modules; this module only mounts them under
/api so app.main and existing imports keep working unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.routers import (
    accounts,
    auth,
    deductions,
    declaration_statistics,
    employees,
    files,
    governance,
    hr_admin,
    organization,
    recognitions,
    sick_leaves,
    sick_leave_import,
    statistics,
)

from app.routers._shared import is_previewable_image, preview_kind  # noqa: F401  (re-exported)


router = APIRouter(prefix="/api", tags=["v2"])

for _module in (
    accounts,
    auth,
    deductions,
    declaration_statistics,
    employees,
    files,
    governance,
    hr_admin,
    organization,
    recognitions,
    sick_leaves,
    sick_leave_import,
    statistics,
):
    router.include_router(_module.router)
