"""Daytime wall-clock source for isolated formal-rotation fixtures only."""
from datetime import datetime as RealDateTime
import sys


class RotationWallClock(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return RealDateTime.now(tz).replace(hour=12, minute=0, second=0, microsecond=0)


def install_rotation_test_clock(patch):
    # Patch already-loaded fixture/application clock bindings, never import the
    # application before a fixture has selected its isolated data directory.
    prefixes = ('test_rotation_', 'app.rotation', 'app.routers.rotation_board',
                'app.routers.auth', 'app.v2_auth')
    for name, module in list(sys.modules.items()):
        if name.startswith(prefixes):
            value = getattr(module, 'datetime', None)
            if isinstance(value, type) and issubclass(value, RealDateTime):
                patch.setattr(module, 'datetime', RotationWallClock)
    temporary = sys.modules.get('app.rotation.temporary')
    if temporary is not None:
        # SQLAlchemy captures this callable when the model is imported, before
        # the test clock is installed. Keep identity creation and event clocks
        # consistent so historical temporary-identity markers remain meaningful.
        default = temporary.TemporaryMember.__table__.c.created_at.default
        patch.setattr(default, 'arg', lambda context: RotationWallClock.now())
