"""Krita node lifecycle for one Loomis head instance.

Each head owns a single vector layer at the document root. The layer name is
discovered on document change, so multiple heads can coexist in one document.
"""

import math
import re

from . import limits, timing
from .config import LIVE_SAMPLES
from .loomis_head_generator import LoomisHead3D

LAYER_PREFIX = "_HeadGuide"

SCALE_TOLERANCE = 0.003
NORMALIZE_SETTLE_POLLS = 3


def scaled_placement(center, offset, scale, factor, minimum, maximum):
    """Return (new_scale, new_position) after scaling about any pivot.

    ``offset`` is the model position minus the geometry center at the last
    redraw, so it grows with the geometry and keeps the head anchored.
    """
    new_scale = max(minimum, min(maximum, scale * factor))
    effective = new_scale / scale if scale else 1.0
    position = (
        center[0] + effective * offset[0],
        center[1] + effective * offset[1],
    )
    return new_scale, position

_NAME_RE = re.compile(r"^_HeadGuide_(\d+)$")
_LEGACY_RE = re.compile(r"^Loomis Method Head Guide(?: (\d+))?$")


def is_head_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or "") or _LEGACY_RE.match(name or ""))


def _name_index(name: str) -> int:
    match = _NAME_RE.match(name or "")
    if match:
        return int(match.group(1))
    match = _LEGACY_RE.match(name or "")
    if not match:
        return 0
    number = match.group(1)
    return int(number) if number else 1


def next_name(doc) -> str:
    """Return the next free head layer name for a document."""
    highest = 0
    for node in iter_vector_layers(doc):
        try:
            name = node.name()
        except Exception:
            continue
        highest = max(highest, _name_index(name))
    return "%s_%d" % (LAYER_PREFIX, highest + 1)


def iter_vector_layers(doc) -> list[object]:
    """Return every vector layer, including layers nested in groups."""
    try:
        root = doc.rootNode()
    except Exception:
        return []

    try:
        return list(root.findChildNodes("", True, False, "vectorlayer"))
    except (AttributeError, TypeError, RuntimeError):
        pass

    found: list[object] = []

    def walk(nodes):
        for node in nodes:
            try:
                node_type = node.type()
            except Exception:
                continue
            if node_type == "vectorlayer":
                found.append(node)
            try:
                children = node.childNodes()
            except Exception:
                children = []
            if children:
                walk(children)

    try:
        walk(root.childNodes())
    except Exception:
        pass
    return found


def discover(doc) -> list[tuple[str, object]]:
    """Return (layer name, vector layer) for existing heads in the document."""
    found: list[tuple[str, object]] = []
    for layer in iter_vector_layers(doc):
        try:
            name = layer.name()
        except Exception:
            continue
        if is_head_name(name):
            found.append((name, layer))
    return found


class HeadInstance:
    def __init__(self, name, layer, model: LoomisHead3D, point_scale=(1.0, 1.0)):
        self.name = name
        self.layer = layer
        self.model = model
        self.point_scale_x = float(point_scale[0])
        self.point_scale_y = float(point_scale[1])
        self.last_shape_states = self.shape_states()
        self.last_bounds = self._layer_bounds()
        self.last_placement = None
        self.needs_normalize = False
        self.stable_polls = 0
        self._refresh_placement_baseline()
        self.preset_name: str | None = None
        self.preset_dirty = False

    def _layer_bounds(self):
        """Cheap layer-level bounds used to skip per-shape polling when idle."""
        try:
            box = self.layer.bounds()
        except Exception:
            return None
        if box is None or box.isNull() or box.isEmpty():
            return None
        return (box.x(), box.y(), box.width(), box.height())

    @staticmethod
    def _point_scale(doc):
        """Pixels per document point; Krita shape coordinates are in points."""
        try:
            return (doc.xRes() / 72.0, doc.yRes() / 72.0)
        except (AttributeError, RuntimeError, TypeError):
            return (1.0, 1.0)

    @classmethod
    def create(cls, doc, model: LoomisHead3D | None = None, name: str | None = None) -> "HeadInstance":
        name = name or next_name(doc)
        layer = doc.createVectorLayer(name)
        doc.rootNode().addChildNode(layer, None)

        if model is None:
            model = LoomisHead3D()
            model.set_position_x(doc.width() // 2)
            model.set_position_y(doc.height() // 2)

        instance = cls(name, layer, model, cls._point_scale(doc))
        return instance

    @classmethod
    def attach(cls, doc, name: str, layer, model: LoomisHead3D | None = None) -> "HeadInstance":
        instance = cls(
            name,
            layer,
            model if model is not None else LoomisHead3D(),
            cls._point_scale(doc),
        )
        instance.last_shape_states = instance.shape_states()
        instance._refresh_placement_baseline()
        return instance

    def shape_states(self):
        """Return (name, position, bounds center) for non-arrow shapes."""
        states = []
        try:
            shapes = self.layer.shapes()
        except Exception:
            return states
        for shape in shapes:
            try:
                name = shape.name()
                if "arrow" in (name or "").lower():
                    continue
                point = shape.position()
                box = shape.boundingBox()
                center = None
                if box is not None and not box.isNull() and not box.isEmpty():
                    center = (
                        box.x() + box.width() / 2.0,
                        box.y() + box.height() / 2.0,
                    )
                states.append((name, (float(point.x()), float(point.y())), center))
            except Exception:
                continue
        return states

    def placement_delta(self):
        """Return ((dx, dy), scale factor, current states, bounds changed)."""
        bounds = self._layer_bounds()
        if bounds is not None and bounds == self.last_bounds:
            return None, None, self.last_shape_states, False
        self.last_bounds = bounds

        current = self.shape_states()
        previous = self.last_shape_states
        if previous is None or len(previous) != len(current):
            return None, self.scale_factor(), current, True

        deltas = []
        for (_, prev_pos, prev_center), (_, cur_pos, cur_center) in zip(previous, current):
            dx = (cur_pos[0] - prev_pos[0]) * self.point_scale_x
            dy = (cur_pos[1] - prev_pos[1]) * self.point_scale_y
            if prev_center is not None and cur_center is not None:
                box_dx = (cur_center[0] - prev_center[0]) * self.point_scale_x
                box_dy = (cur_center[1] - prev_center[1]) * self.point_scale_y
                if abs(box_dx) >= 0.5 or abs(box_dy) >= 0.5:
                    dx, dy = box_dx, box_dy
            if abs(dx) >= 0.5 or abs(dy) >= 0.5:
                deltas.append((dx, dy))

        if not deltas:
            return None, self.scale_factor(), current, True
        dx = sum(item[0] for item in deltas) / len(deltas)
        dy = sum(item[1] for item in deltas) / len(deltas)
        return (dx, dy), self.scale_factor(), current, True

    def bounds_center(self):
        """Center of the head geometry, ignoring the detached arrow marker."""
        boxes = []
        try:
            for shape in self.layer.shapes():
                try:
                    if "arrow" in (shape.name() or "").lower():
                        continue
                    box = shape.boundingBox()
                except Exception:
                    continue
                if box is None or box.isNull() or box.isEmpty():
                    continue
                boxes.append(box)
        except Exception:
            return None

        if not boxes:
            try:
                bounds = self.layer.bounds()
                if bounds is None or bounds.isNull() or bounds.isEmpty():
                    return None
                return (bounds.x() + bounds.width() / 2.0, bounds.y() + bounds.height() / 2.0)
            except (AttributeError, RuntimeError, TypeError):
                return None

        left = min(box.x() for box in boxes)
        top = min(box.y() for box in boxes)
        right = max(box.x() + box.width() for box in boxes)
        bottom = max(box.y() + box.height() for box in boxes)
        return ((left + right) / 2.0, (top + bottom) / 2.0)

    def placement_metrics(self):
        """Return geometry center (pixels), extent (points), and scale factor."""
        boxes = []
        factors = []
        try:
            shapes = self.layer.shapes()
        except Exception:
            return None

        for shape in shapes:
            try:
                if "arrow" in (shape.name() or "").lower():
                    continue
            except Exception:
                continue
            try:
                box = shape.boundingBox()
            except Exception:
                box = None
            if box is not None and not box.isNull() and not box.isEmpty():
                boxes.append((box.x(), box.y(), box.width(), box.height()))
            try:
                getter = getattr(shape, "absoluteTransformation", None)
                matrix = getter() if getter is not None else shape.transformation()
                det = matrix.m11() * matrix.m22() - matrix.m12() * matrix.m21()
            except Exception:
                continue
            if det > 0.0 and math.isfinite(det):
                factors.append(math.sqrt(det))

        transform = None
        if factors:
            transform = math.exp(sum(math.log(item) for item in factors) / len(factors))

        if boxes:
            left = min(box[0] for box in boxes)
            top = min(box[1] for box in boxes)
            right = max(box[0] + box[2] for box in boxes)
            bottom = max(box[1] + box[3] for box in boxes)
            center = (
                (left + right) / 2.0 * self.point_scale_x,
                (top + bottom) / 2.0 * self.point_scale_y,
            )
            extent = (right - left, bottom - top)
            return {"center": center, "extent": extent, "transform": transform}

        try:
            bounds = self.layer.bounds()
        except Exception:
            return None
        if bounds is None or bounds.isNull() or bounds.isEmpty():
            return None
        return {
            "center": (bounds.x() + bounds.width() / 2.0, bounds.y() + bounds.height() / 2.0),
            "extent": (bounds.width(), bounds.height()),
            "transform": transform,
        }

    def _refresh_placement_baseline(self):
        metrics = self.placement_metrics()
        if metrics is not None:
            metrics["offset"] = (
                self.model.position_x - metrics["center"][0],
                self.model.position_y - metrics["center"][1],
            )
        self.last_placement = metrics

    def scale_factor(self):
        """Return the uniform scale ratio since the last redraw, or None."""
        baseline = self.last_placement
        current = self.placement_metrics()
        if baseline is None or current is None:
            return None

        factor = None
        before = baseline.get("transform")
        after = current.get("transform")
        if before is not None and after is not None:
            factor = after / before
        else:
            before_extent = baseline.get("extent")
            after_extent = current.get("extent")
            if before_extent and after_extent:
                ex0, ey0 = before_extent
                ex1, ey1 = after_extent
                if ex0 > 1e-6 and ey0 > 1e-6 and ex1 > 0.0 and ey1 > 0.0:
                    factor = math.sqrt((ex1 / ex0) * (ey1 / ey0))

        if factor is None or abs(factor - 1.0) < SCALE_TOLERANCE:
            return None
        return factor

    def apply_scale_factor(self, factor) -> bool:
        """Absorb a canvas scale gesture into the model without redrawing."""
        baseline = self.last_placement
        current = self.placement_metrics()
        if baseline is None or current is None or factor is None:
            return False
        scale, position = scaled_placement(
            current["center"],
            baseline.get("offset", (0.0, 0.0)),
            self.model.scale,
            factor,
            limits.SCALE.minimum,
            limits.SCALE.maximum,
        )
        self.model.set_scale(scale)
        self.model.set_position_x(position[0])
        self.model.set_position_y(position[1])
        self._refresh_placement_baseline()
        self.last_shape_states = self.shape_states()
        self.last_bounds = self._layer_bounds()
        return True

    def _selected_shape_names(self):
        names = []
        try:
            for shape in self.layer.shapes():
                try:
                    if shape.isSelected():
                        names.append(shape.name())
                except Exception:
                    continue
        except Exception:
            return names
        return names

    def _restore_shape_selection(self, names):
        if not names:
            return
        wanted = set(names)
        try:
            for shape in self.layer.shapes():
                try:
                    if shape.name() in wanted:
                        shape.select()
                except Exception:
                    continue
        except Exception:
            pass

    def redraw(self, doc, samples: int = LIVE_SAMPLES) -> dict:
        started = timing.now()
        svg = self.model.build_svg(
            width=doc.width(),
            height=doc.height(),
            dash_back="8,8",
            samples=samples,
        )
        built = timing.now()

        selected = self._selected_shape_names()
        shapes = self.layer.shapes()
        shapes_read = timing.now()
        remove_times: list[float] = []
        for shape in shapes:
            remove_started = timing.now()
            shape.remove()
            remove_times.append(timing.now() - remove_started)
        removed = timing.now()

        self.layer.addShapesFromSvg(svg)
        self._restore_shape_selection(selected)
        added = timing.now()

        self.last_shape_states = self.shape_states()
        self._refresh_placement_baseline()
        self.needs_normalize = False
        self.stable_polls = 0
        states_read = timing.now()

        self.last_bounds = self._layer_bounds()
        finished = timing.now()

        return {
            "total": finished - started,
            "build_svg": built - started,
            "shapes_call": shapes_read - built,
            "remove": removed - shapes_read,
            "add": added - removed,
            "states": states_read - added,
            "bounds": finished - states_read,
            "shape_count": len(shapes),
            "samples": samples,
            "remove_each": remove_times,
        }

    def reveal(self, doc) -> None:
        try:
            self.layer.setVisible(True)
            doc.setActiveNode(self.layer)
        except Exception:
            pass

    def remove(self) -> None:
        try:
            self.layer.remove()
        except Exception:
            pass
