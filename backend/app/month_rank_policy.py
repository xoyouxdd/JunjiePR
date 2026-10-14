"""Unreleased personal rank policy; independent of PR scoring and permissions."""
import os

MONTH_RANK_RULE_VERSION = "midrank-v2-min10"
MIN_RANK_COHORT = 10


def monthly_ranks_enabled() -> bool:
    return os.environ.get("RECOGNITION_ENABLE_MONTHLY_RANKS", "1").strip() == "1"
