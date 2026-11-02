"""Per-document head placement stored in a Krita document annotation.

Presets are global visual configuration. Each head instance's position,
direction, and size belong to the document, keyed by the layer's persistent
unique id. This module stays free of Krita and PyQt imports so it can be unit
tested.
"""

import copy
import json
import math

ANNOTATION_TYPE = "loomis_head_local_state"
ANNOTATION_DESCRIPTION = "Loomis head positions and directions"
SCHEMA_VERSION = 1


def empty_state() -> dict:
    return {"version": SCHEMA_VERSION, "instances": {}}


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value, length: int):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        return None
    if not all(_finite(item) for item in value):
        return None
    return [float(item) for item in value]


def _quaternion(value):
    result = _vector(value, 4)
    if result is None:
        return None
    if math.sqrt(sum(item * item for item in result)) <= 1e-6:
        return None
    return result


def loads(payload) -> dict:
    """Parse annotation bytes/text, returning an empty state on any damage."""
    if not isinstance(payload, (bytes, bytearray, str)):
        data_method = getattr(payload, "data", None)
        if callable(data_method):
            try:
                payload = data_method()
            except Exception:
                return empty_state()
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = bytes(payload).decode("utf-8")
        except (UnicodeDecodeError, TypeError):
            return empty_state()
    if not isinstance(payload, str) or not payload:
        return empty_state()

    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()

    raw = data.get("instances")
    if not isinstance(raw, dict):
        return empty_state()

    instances = {}
    for key, entry in raw.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        item = {}
        name = entry.get("name")
        if isinstance(name, str) and name:
            item["name"] = name
        position = _vector(entry.get("position"), 2)
        if position is not None:
            item["position"] = position
        quaternion = _quaternion(entry.get("quaternion"))
        if quaternion is not None:
            item["quaternion"] = quaternion
        scale = entry.get("scale")
        if _finite(scale) and scale > 0.0:
            item["scale"] = float(scale)
        config = entry.get("config")
        if isinstance(config, dict):
            item["config"] = copy.deepcopy(config)
        preset = entry.get("preset")
        if isinstance(preset, str) and preset:
            item["preset"] = preset
        if entry.get("preset_dirty") is True:
            item["preset_dirty"] = True
        if item:
            instances[key] = item

    return {"version": SCHEMA_VERSION, "instances": instances}


def dumps(state: dict) -> str:
    return json.dumps(state, indent=2, sort_keys=True) + "\n"


def lookup(state: dict, uid: str, name: str | None = None) -> dict | None:
    """Find an entry by layer id, falling back to the layer name."""
    instances = state.get("instances", {}) if isinstance(state, dict) else {}
    entry = instances.get(uid)
    if isinstance(entry, dict):
        return entry
    if name:
        for item in instances.values():
            if isinstance(item, dict) and item.get("name") == name:
                return item
    return None


def remember(
    state: dict,
    uid: str,
    name: str,
    position,
    quaternion,
    config=None,
    preset=None,
    preset_dirty: bool = False,
    scale=None,
) -> bool:
    """Store one instance placement/config/preset; return True when changed."""
    instances = state.setdefault("instances", {})
    new = {"name": name}

    position = _vector(position, 2)
    if position is not None:
        new["position"] = position
    quaternion = _quaternion(quaternion)
    if quaternion is not None:
        new["quaternion"] = quaternion
    if _finite(scale) and scale > 0.0:
        new["scale"] = float(scale)
    if isinstance(config, dict):
        new["config"] = copy.deepcopy(config)
    if isinstance(preset, str) and preset:
        new["preset"] = preset
    if preset_dirty:
        new["preset_dirty"] = True

    if uid not in instances and name:
        for key, entry in list(instances.items()):
            if isinstance(entry, dict) and entry.get("name") == name:
                del instances[key]

    if instances.get(uid) == new:
        return False
    instances[uid] = new
    return True


def rename(state: dict, uid: str, name: str) -> bool:
    entry = state.get("instances", {}).get(uid) if isinstance(state, dict) else None
    if not isinstance(entry, dict) or entry.get("name") == name:
        return False
    entry["name"] = name
    return True


def discard(state: dict, uid: str) -> bool:
    instances = state.get("instances", {}) if isinstance(state, dict) else {}
    if uid not in instances:
        return False
    del instances[uid]
    return True
