"""Built-in and user head presets.

User presets store reusable visual configuration only. Per-document position
and direction live in ``local_state.py``. Built-in orientation presets are the
exception: they intentionally carry a direction.

This module deliberately avoids importing Krita or PyQt so it can be tested
with plain Python. The docker supplies the application data directory.
"""

import copy
import json
import os
import shutil
import tempfile

from . import diag

from .linalg import q_axis_angle, q_mul

SCHEMA_VERSION = 1

BUILTIN_ORDER = (
    "Front",
)


def _quaternion(yaw_deg: float, pitch_deg: float = 0.0):
    q_yaw = q_axis_angle([0.0, 1.0, 0.0], yaw_deg * 3.141592653589793 / 180.0)
    q_pitch = q_axis_angle([1.0, 0.0, 0.0], pitch_deg * 3.141592653589793 / 180.0)
    return q_mul(q_yaw, q_pitch)


def _builtin_model(yaw: float = 0.0, pitch: float = 0.0) -> dict:
    from .loomis_head_generator import LoomisHead3D

    model = LoomisHead3D()
    model.set_quaternion(_quaternion(yaw, pitch))
    return model.to_preset_dict()


BUILTIN_PRESETS: dict[str, dict] = {
    "Front": _builtin_model(),
}


def builtin_presets() -> dict[str, dict]:
    """Return a deep copy so callers can apply presets without cross-talk."""
    return copy.deepcopy(BUILTIN_PRESETS)


def builtin_names() -> list[str]:
    return list(BUILTIN_ORDER)


def preset_label(name: str, is_builtin: bool, dirty: bool = False) -> str:
    """Return the combo-box label for a preset, including the dirty marker."""
    label = "%s (built-in)" % name if is_builtin else str(name)
    if dirty:
        label += " *"
    return label

def clean_name(name: str) -> str:
    cleaned = " ".join(str(name).split()).strip()
    if not cleaned:
        raise ValueError("preset name is empty")
    if len(cleaned) > 64:
        cleaned = cleaned[:64].strip()
    if any(ord(ch) < 32 for ch in cleaned):
        raise ValueError("preset name contains control characters")
    return cleaned


class PresetStore:
    """JSON-backed user preset storage with atomic writes."""

    def __init__(self, directory: str) -> None:
        self.directory = directory
        self.path = os.path.join(directory, "presets.json")

    def _backup_corrupt(self) -> None:
        """Keep the damaged file so the next save cannot silently erase it."""
        try:
            if os.path.exists(self.path):
                shutil.copyfile(self.path, self.path + ".bak")
        except OSError as exc:
            diag.warn("could not back up corrupt presets file: %s" % exc)

    def _load_file(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            self._backup_corrupt()
            return {}
        if not isinstance(data, dict):
            self._backup_corrupt()
            return {}
        presets = data.get("presets")
        if not isinstance(presets, dict):
            self._backup_corrupt()
            return {}
        return {
            str(name): model
            for name, model in presets.items()
            if isinstance(name, str) and isinstance(model, dict)
        }

    def _write_file(self, presets: dict) -> None:
        os.makedirs(self.directory, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "presets": presets}
        fd, temp_path = tempfile.mkstemp(prefix=".presets-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_path, self.path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def user_presets(self) -> dict[str, dict]:
        return self._load_file()

    def save(self, name: str, model: dict) -> str:
        cleaned = clean_name(name)
        if cleaned in BUILTIN_PRESETS:
            raise ValueError("built-in preset name is reserved")
        presets = self._load_file()
        presets[cleaned] = copy.deepcopy(model)
        self._write_file(presets)
        return cleaned

    def delete(self, name: str) -> bool:
        if name in BUILTIN_PRESETS:
            return False
        presets = self._load_file()
        if name not in presets:
            return False
        del presets[name]
        self._write_file(presets)
        return True

    def all_presets(self) -> list[tuple[str, dict, bool]]:
        """Return (name, model, is_builtin), built-ins first."""
        result: list[tuple[str, dict, bool]] = [
            (name, copy.deepcopy(BUILTIN_PRESETS[name]), True) for name in BUILTIN_ORDER
        ]
        user = self._load_file()
        for name in sorted(user, key=str.lower):
            if name in BUILTIN_PRESETS:
                continue
            result.append((name, copy.deepcopy(user[name]), False))
        return result

    def get(self, name: str) -> dict | None:
        """Return one preset by name, or None when it no longer exists."""
        if not name:
            return None
        if name in BUILTIN_PRESETS:
            return copy.deepcopy(BUILTIN_PRESETS[name])
        return copy.deepcopy(self._load_file().get(name))
