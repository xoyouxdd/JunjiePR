"""Role, recognition eligibility and score precision policy constants.

Acting duties add permissions without replacing the base identity. Supervisor
own-score eligibility starts in 2026-10; earlier totals keep their old policy.
"""
from decimal import Decimal


FRONTLINE_CODES = {"CM", "TR"}


LEADER_CODES = {"TA_SUPERVISOR", "SUPERVISOR"}


GSM_CODES = {"TA_GSM", "GSM"}


RECOGNIZER_CODES = {"TA_SUPERVISOR", "SUPERVISOR", "TA_GSM", "GSM", "AM", "OM"}


SENIOR_RECOGNIZER_CODES = {"TA_GSM", "GSM", "AM", "OM"}


ACTING_TA_GSM_RECOGNIZER_CODES = {"GSM", "AM", "OM"}


RECOGNIZER_CIRCLE_ORDER = {"热力追踪": 0, "矮人迷宫": 1, "小熊罐子": 2}


RECOGNIZER_ROLE_ORDER = {
    "TA_SUPERVISOR": 0,
    "SUPERVISOR": 1,
    "TA_GSM": 0,
    "GSM": 1,
    "AM": 2,
    "OM": 3,
}


RECOGNIZER_ELIGIBILITY_START = "2026-08-01"


SCORE_UNIT = Decimal("0.01")


DUTY_ROLE_CODES = {"TA_SUPERVISOR", "TA_GSM"}


DUTY_BASE_CODES = {"TA_SUPERVISOR": FRONTLINE_CODES, "TA_GSM": {"SUPERVISOR"}}


SCORING_CATEGORY_BY_CODE = {"CM": "frontline", "TR": "frontline", "SUPERVISOR": "supervisor"}


SCORED_BASE_CODES = set(SCORING_CATEGORY_BY_CODE)


SUPERVISOR_SCORING_START_MONTH = "2026-10"
