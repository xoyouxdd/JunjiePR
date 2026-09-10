"""Calendar edition versions.

APP_VERSION is YYYY.MM.DD.N: calendar date plus the nth edition that day
(1-based). STATIC_CACHE_VERSION stays equal to APP_VERSION so browsers
drop cached CSS/JS whenever a new edition ships.

Same day → increment N. New day → YYYY.MM.DD.1.
"""

APP_VERSION = "2026.09.10.5"
STATIC_CACHE_VERSION = APP_VERSION
