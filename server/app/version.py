"""Single source of truth for application and static-cache versions.

Bump APP_VERSION for every release; STATIC_CACHE_VERSION only needs a new
marker when HTML/CSS/JS assets referenced by the app shell change, so that
browsers drop their cached copies.
"""

APP_VERSION = "2.23.74"
STATIC_CACHE_VERSION = "2.23.74-20260909-direct-entrypoint-r1"
