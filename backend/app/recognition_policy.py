"""Recognition types, caps and effective stored credit."""
from __future__ import annotations

from app.v2_models import RecognitionRecord
from datetime import date
from decimal import Decimal


SPECIAL_RECOGNITION_TYPES = {
    "MSP": {"option_id": "special:MSP", "name": "MSP", "score": Decimal("3.00"), "monthly_limit": None},
    "COMMENDATION_LETTER": {
        "option_id": "special:COMMENDATION_LETTER",
        "name": "表扬信",
        "score": Decimal("3.00"),
        "monthly_limit": 1,
    },
}


DEDICATED_RECOGNITION_TYPE_CODES = {"POC"}


MONTHLY_CATEGORY_CAP_CODES = {"SAFETY", "COURTESY", "INCLUSION", "EFFICIENCY", "SHOW"}


MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE = date(2026, 9, 1)


MONTHLY_CATEGORY_CAP_LIMIT = Decimal("5.00")


def effective_recognition_credit(row: RecognitionRecord) -> Decimal:
    """Legacy records before the 2026-09-01 cap retain their raw score."""
    if row.recognition_date and date.fromisoformat(row.recognition_date) < MONTHLY_CATEGORY_CAP_EFFECTIVE_DATE:
        return Decimal(row.fraction or 0)
    return Decimal(row.credited_fraction or 0)
