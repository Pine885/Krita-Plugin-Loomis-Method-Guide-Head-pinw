"""Single source of truth for model limits and defaults.

The generator clamps to these values, the docker builds its slider ranges from
them, and the built-in Front preset is derived from the generator defaults.
"""

from typing import NamedTuple


class Limit(NamedTuple):
    minimum: float
    maximum: float
    default: float

    def clamp(self, value: float) -> float:
        return max(self.minimum, min(self.maximum, value))


RADIUS = Limit(0.05, 10.0, 1.0)
SCALE = Limit(0.10, 4.0, 0.40)
SIDE_CUT = Limit(0.05, 1.0, 0.66)
SIDE_CUT_GEOMETRY_MAX = 0.95
FACE_AGE = Limit(0.0, 1.0, 0.75)
CHIN_WIDTH = Limit(0.0, 1.5, 1.0)
FRONT_LINE_STROKE = Limit(1.0, 12.0, 10.0)
BACK_LINE_STROKE = Limit(0.0, 12.0, 7.0)
