import os

from krita import DockWidget, DockWidgetFactory, DockWidgetFactoryBase, Extension, Krita
from PyQt5 import uic
from PyQt5.QtCore import QByteArray, QStandardPaths, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QColorDialog,
    QFrame,
    QInputDialog,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import limits, local_state, timing
from .config import (
    LIVE_SAMPLES,
    LOCAL_SAVE_DEBOUNCE,
    RECONCILE_INTERVAL,
    REDRAW_DEBOUNCE,
    SYNC_INTERVAL,
)
from .head_instance import (
    NORMALIZE_SETTLE_POLLS,
    HeadInstance,
    _name_index,
    is_head_name,
    iter_vector_layers,
)
from .linalg import q_identity
from .loomis_head_generator import LoomisHead3D
from .presets import PresetStore, builtin_names
from .trackball import TrackballWidget

PRESET_FOLDER = "loomis_method_head_guide"


def _doc_key(doc) -> str:
    try:
        return str(doc.rootNode().uniqueId())
    except Exception:
        return str(id(doc))


def _node_key(node) -> str:
    try:
        return str(node.uniqueId())
    except Exception:
        return str(id(node))


def _default_model() -> LoomisHead3D:
    return LoomisHead3D()


class DocumentState:
    def __init__(self, doc):
        self.doc = doc
        self.instances: dict[str, HeadInstance] = {}
        self.active_id: str | None = None
        self.local = local_state.empty_state()

    def active(self) -> HeadInstance | None:
        if self.active_id is None:
            return None
        return self.instances.get(self.active_id)


class LoomisProportionsDocker(DockWidget):
    def __init__(self):
        super().__init__()

        self.doc = None
        self.state = None
        self.states: dict[str, DocumentState] = {}
        self._fallback_model = _default_model()
        self.update_scheduled = False
        self._drawing = False
        self.local_dirty = False
        self._last_sync = 0.0

        self.draw_timer = QTimer(self)
        self.draw_timer.setSingleShot(True)
        self.draw_timer.setInterval(REDRAW_DEBOUNCE)
        self.draw_timer.timeout.connect(self._draw_pending)

        self.local_save_timer = QTimer(self)
        self.local_save_timer.setSingleShot(True)
        self.local_save_timer.setInterval(LOCAL_SAVE_DEBOUNCE)
        self.local_save_timer.timeout.connect(self._write_local_state)

        self.setWindowTitle("Loomis Method Head Guide")
        self.setMinimumSize(200, 160)

        app_data = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) or os.path.expanduser(
            "~/.local/share/krita"
        )
        self.preset_store = PresetStore(os.path.join(app_data, PRESET_FOLDER))

        self.connect_ui()
        self.refresh_preset_combo()

        # Krita does not expose a dependable cross-tool signal for every vector
        # shape move/transform, so watch the active vector layer bounds.
        self.position_sync_timer = QTimer(self)
        self.position_sync_timer.setInterval(120)
        self.position_sync_timer.timeout.connect(self.sync_manual_position)
        self.position_sync_timer.start()

        # Full layer-tree scans are comparatively expensive; run them slowly
        # and keep the 120 ms path limited to the active layer.
        self.reconcile_timer = QTimer(self)
        self.reconcile_timer.setInterval(RECONCILE_INTERVAL)
        self.reconcile_timer.timeout.connect(self._reconcile_instances)
        self.reconcile_timer.start()

        try:
            Krita.instance().notifier().applicationClosing.connect(self._flush_local_state)
        except Exception:
            pass

        self._attach_document(Krita.instance().activeDocument())

    # --- active model / node -------------------------------------------

    def active_state(self) -> DocumentState | None:
        if self.doc is None:
            return None
        return self.state

    def active_instance(self) -> HeadInstance | None:
        state = self.active_state()
        return state.active() if state is not None else None

    @property
    def loomis_head(self) -> LoomisHead3D:
        instance = self.active_instance()
        return instance.model if instance is not None else self._fallback_model

    @property
    def loomis_layer(self):
        instance = self.active_instance()
        return instance.layer if instance is not None else None

    # --- document / instance bookkeeping --------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._attach_document(Krita.instance().activeDocument())
        self.position_sync_timer.start()
        self.reconcile_timer.start()

    def hideEvent(self, event):
        self.flush_pending_draw()
        self._flush_local_state()
        self.position_sync_timer.stop()
        self.reconcile_timer.stop()
        super().hideEvent(event)

    def canvasChanged(self, canvas):
        if canvas is not None and canvas.view() is not None:
            doc = canvas.view().document()
        else:
            doc = Krita.instance().activeDocument()

        self._attach_document(doc)

    def _attach_document(self, doc):
        if doc is None:
            if self.doc is not None:
                self.flush_pending_draw()
                self._flush_local_state()
            self.doc = None
            self.state = None
            self.refresh_instance_combo()
            self.sync_ui_from_model()
            return

        key = _doc_key(doc)
        old_key = _doc_key(self.doc) if self.doc is not None else None
        doc_changed = key != old_key
        if doc_changed:
            self.flush_pending_draw()
            self._flush_local_state()

        state = self.states.get(key)
        if state is None:
            state = DocumentState(doc)
            state.local = self._read_local_state(doc)
            self.states[key] = state
        elif doc_changed:
            state.local = self._read_local_state(doc)
            state.instances.clear()
            state.active_id = None
        elif not state.local.get("instances"):
            state.local = self._read_local_state(doc)
        state.doc = doc
        self.doc = doc
        self.state = state
        self._last_sync = 0.0

        self._reconcile_instances()
        self.refresh_instance_combo()
        self.sync_ui_from_model()

    def _read_local_state(self, doc):
        try:
            payload = doc.annotation(local_state.ANNOTATION_TYPE)
        except Exception:
            return local_state.empty_state()
        if not payload:
            return local_state.empty_state()
        return local_state.loads(payload)

    def _write_local_state(self):
        self.local_save_timer.stop()
        state = self.active_state()
        if not self.local_dirty or state is None or self.doc is None:
            return
        payload = local_state.dumps(state.local).encode("utf-8")
        try:
            self.doc.setAnnotation(
                local_state.ANNOTATION_TYPE,
                local_state.ANNOTATION_DESCRIPTION,
                QByteArray(payload),
            )
        except Exception:
            return
        self.local_dirty = False

    def _flush_local_state(self):
        self.local_save_timer.stop()
        if self.local_dirty:
            self._write_local_state()

    def _schedule_local_save(self):
        self.local_dirty = True
        self.local_save_timer.start()

    def _remember_instance(self, instance=None):
        state = self.active_state()
        if instance is None and state is not None:
            instance = state.active()
        if state is None or instance is None:
            return False
        placement = instance.model.to_local_dict()
        changed = local_state.remember(
            state.local,
            _node_key(instance.layer),
            instance.name,
            placement["position"],
            placement["quaternion"],
            config=instance.model.to_config_dict(),
            preset=instance.preset_name,
            preset_dirty=instance.preset_dirty,
            scale=placement["scale"],
        )
        if changed:
            self._schedule_local_save()
        return changed

    def _forget_instance(self, instance):
        state = self.active_state()
        if state is None or instance is None:
            return
        if local_state.discard(state.local, _node_key(instance.layer)):
            self._schedule_local_save()

    def _reconcile_instances(self):
        """Match tracked instances against the document's current layers."""
        state = self.active_state()
        if state is None or self.doc is None:
            return

        current: dict[str, object] = {}
        for node in iter_vector_layers(self.doc):
            current[_node_key(node)] = node

        changed = False
        for key in list(state.instances):
            if key not in current:
                state.instances.pop(key, None)
                if state.active_id == key:
                    state.active_id = None
                if local_state.discard(state.local, key):
                    self._schedule_local_save()
                changed = True

        for key, instance in list(state.instances.items()):
            node = current.get(key)
            if node is None:
                continue
            instance.layer = node
            try:
                name = node.name()
            except Exception:
                name = instance.name
            if name != instance.name:
                instance.name = name
                if local_state.rename(state.local, key, name):
                    self._schedule_local_save()
                changed = True

        for key, layer in current.items():
            if key in state.instances:
                continue
            try:
                name = layer.name()
            except Exception:
                continue
            if not is_head_name(name):
                continue
            instance = HeadInstance.attach(self.doc, name, layer)
            entry = local_state.lookup(state.local, key, name)
            if entry is not None:
                instance.model.apply_local_dict(entry)
                config = entry.get("config")
                if isinstance(config, dict):
                    instance.model.apply_config_dict(config)
                    if "scale" not in entry and "scale" in config:
                        instance.model.apply_local_dict({"scale": config["scale"]})
                preset = entry.get("preset")
                if isinstance(preset, str) and preset:
                    instance.preset_name = preset
                instance.preset_dirty = entry.get("preset_dirty") is True
            else:
                center = instance.bounds_center()
                if center is not None:
                    instance.model.set_position_x(center[0] * instance.point_scale_x)
                    instance.model.set_position_y(center[1] * instance.point_scale_y)
            instance._refresh_placement_baseline()
            state.instances[key] = instance
            changed = True

        active_id = None
        try:
            active_node = self.doc.activeNode()
        except Exception:
            active_node = None
        if active_node is not None:
            key = _node_key(active_node)
            if key in state.instances:
                active_id = key

        if active_id != state.active_id:
            previous = state.instances.get(state.active_id) if state.active_id is not None else None
            if previous is not None:
                self._adopt_instance_placement(previous)
            instance = state.instances.get(active_id) if active_id is not None else None
            if instance is not None:
                self._adopt_instance_placement(instance)
            state.active_id = active_id
            changed = True

        if changed:
            self.refresh_instance_combo()
            self.sync_ui_from_model()

    def _sync_active_reference(self):
        """Cheap active-layer check; avoid rescanning the layer tree.

        `doc.activeNode()` occasionally stalls for 10-17 ms, so it must not run
        on every slider tick or redraw frame. Poll it at most every
        `SYNC_INTERVAL` ms; the 120 ms position timer still catches changes.
        """
        state = self.active_state()
        if state is None or self.doc is None:
            return
        now = timing.now()
        if now - self._last_sync < SYNC_INTERVAL / 1000.0:
            return
        self._last_sync = now
        try:
            node = self.doc.activeNode()
        except Exception:
            node = None
        key = _node_key(node) if node is not None else None

        if key is not None and key in state.instances:
            if key != state.active_id:
                self._activate_instance(key)
            return

        if state.active_id is not None:
            self._reconcile_instances()

    def _add_head(self, state, model=None):
        self.flush_pending_draw()
        if model is None:
            model = _default_model()
            model.set_position_x(state.doc.width() // 2)
            model.set_position_y(state.doc.height() // 2)
        instance = HeadInstance.create(state.doc, model)
        state.instances[_node_key(instance.layer)] = instance
        state.active_id = _node_key(instance.layer)
        self.refresh_instance_combo()
        self.sync_ui_from_model()
        instance.reveal(state.doc)
        self._remember_instance(instance)
        self._flush_local_state()
        self.schedule_update()

    def add_head_clicked(self):
        state = self.active_state()
        if state is None:
            return
        self._add_head(state)

    def duplicate_head_clicked(self):
        state = self.active_state()
        instance = state.active() if state is not None else None
        if instance is None:
            return

        model = LoomisHead3D()
        model.apply_preset_dict(instance.model.to_preset_dict())
        model.set_scale(instance.model.scale)
        offset = max(40, int(min(state.doc.width(), state.doc.height()) * 0.08))
        model.set_position_x(instance.model.position_x + offset)
        model.set_position_y(instance.model.position_y + offset)
        self._add_head(state, model)

    def delete_head_clicked(self):
        state = self.active_state()
        instance = state.active() if state is not None else None
        if instance is None:
            return

        answer = QMessageBox.question(
            self,
            "Delete head",
            "Delete “%s”?" % instance.name,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        self.flush_pending_draw()
        self._forget_instance(instance)
        instance.remove()
        for key, item in list(state.instances.items()):
            if item is instance:
                state.instances.pop(key, None)
                if state.active_id == key:
                    state.active_id = None
                break
        self._flush_local_state()
        self.refresh_instance_combo()
        self.sync_ui_from_model()

    def select_instance(self, index):
        state = self.active_state()
        if state is None or index < 0:
            return
        key = self.ui.headCombo.itemData(index)
        if key not in state.instances or key == state.active_id:
            return
        self.flush_pending_draw()
        try:
            self.doc.setActiveNode(state.instances[key].layer)
        except Exception:
            pass
        self._activate_instance(key)

    def _adopt_instance_placement(self, instance):
        """Absorb moves and scales made while this head was not active."""
        delta, factor, current, changed = instance.placement_delta()
        instance.last_shape_states = current
        if factor is not None:
            instance.apply_scale_factor(factor)
            instance.needs_normalize = True
            self._remember_instance(instance)
        elif delta is not None:
            instance.model.set_position_x(instance.model.position_x + delta[0])
            instance.model.set_position_y(instance.model.position_y + delta[1])
            instance._refresh_placement_baseline()
            self._remember_instance(instance)

    def _activate_instance(self, key):
        state = self.active_state()
        if state is None or key not in state.instances or key == state.active_id:
            return
        previous = state.active()
        if previous is not None:
            self._adopt_instance_placement(previous)
        self._adopt_instance_placement(state.instances[key])
        state.active_id = key
        self.refresh_instance_combo()
        self.sync_ui_from_model()

    def refresh_instance_combo(self):
        combo = self.ui.headCombo
        state = self.active_state()
        combo.blockSignals(True)
        combo.clear()
        if state is not None:
            for key, instance in sorted(
                state.instances.items(),
                key=lambda item: (_name_index(item[1].name), item[1].name),
            ):
                combo.addItem(instance.name, key)
            if state.active_id is not None:
                index = combo.findData(state.active_id)
                combo.setCurrentIndex(index)
            else:
                combo.setCurrentIndex(-1)
        combo.blockSignals(False)

        has_instance = state is not None and state.active() is not None
        self.ui.headCombo.setEnabled(bool(state and state.instances))
        self.ui.addHeadButton.setEnabled(state is not None)
        self.ui.duplicateHeadButton.setEnabled(has_instance)
        self.ui.deleteHeadButton.setEnabled(has_instance)
        self._set_editing_enabled(has_instance)

    def _apply_limit_ranges(self):
        """Build slider ranges from limits.py so the .ui cannot drift."""
        self.ui.sideCutSlider.setRange(
            round(limits.SIDE_CUT.minimum * 100), round(limits.SIDE_CUT.maximum * 100)
        )
        self.ui.faceAgeSlider.setRange(
            round(limits.FACE_AGE.minimum * 100), round(limits.FACE_AGE.maximum * 100)
        )
        self.ui.chinWidthSlider.setRange(
            round(limits.CHIN_WIDTH.minimum * 100), round(limits.CHIN_WIDTH.maximum * 100)
        )
        self.ui.frontStrokeSlider.setRange(
            round(limits.FRONT_LINE_STROKE.minimum),
            round(limits.FRONT_LINE_STROKE.maximum),
        )
        self.ui.backStrokeSlider.setRange(
            round(limits.BACK_LINE_STROKE.minimum),
            round(limits.BACK_LINE_STROKE.maximum),
        )

    def _set_editing_enabled(self, enabled):
        widgets = [
            self.ui.sideCutSlider,
            self.ui.faceAgeSlider,
            self.ui.chinWidthSlider,
            self.ui.frontStrokeSlider,
            self.ui.backStrokeSlider,
            self.ui.showFace,
            self.ui.showArrow,
            self.ui.showSilhouette,
            self.ui.showSideRims,
            self.ui.showSideCross,
            self.ui.strokeColorButton,
            self.ui.trackballHost,
            self.ui.resetButton,
            self.ui.resetSettingsButton,
        ]
        for widget in widgets:
            widget.setEnabled(enabled)
        self._update_preset_buttons()

    def with_schedule_update(self, fn, preset_change=True):
        instance = self.active_instance()
        if instance is None:
            return
        fn()
        if preset_change:
            self._mark_preset_dirty(instance)
        self.schedule_update()

    def _mark_preset_dirty(self, instance=None):
        instance = instance or self.active_instance()
        if instance is None or not instance.preset_name or instance.preset_dirty:
            return
        instance.preset_dirty = True
        self._sync_preset_selection()
        self._remember_instance(instance)

    def _sync_preset_selection(self):
        combo = self.ui.presetCombo
        for index in range(combo.count()):
            text = combo.itemText(index)
            if text.endswith(" *"):
                combo.setItemText(index, text[:-2])

        instance = self.active_instance()
        name = instance.preset_name if instance is not None else None
        combo.blockSignals(True)
        index = combo.findData(name) if name else -1
        combo.setCurrentIndex(index)
        if index >= 0 and instance is not None and instance.preset_dirty:
            combo.setItemText(index, combo.itemText(index) + " *")
        combo.blockSignals(False)
        reset_button = self.ui.resetSettingsButton
        if index >= 0 and name:
            reset_button.setToolTip("Reset to preset “%s”." % name)
        else:
            reset_button.setToolTip("Reset to global defaults.")
        self._update_preset_buttons()

    def _sync_color_swatch(self):
        self.ui.strokeColorSwatch.setStyleSheet(
            "background-color: %s; border: 1px solid #666;" % self.loomis_head.stroke_color
        )

    def pick_stroke_color(self):
        instance = self.active_instance()
        if instance is None:
            return
        old_hex = self.loomis_head.stroke_color
        old_qcolor = QColor(old_hex)
        old_dirty = instance.preset_dirty

        dlg = QColorDialog(self)
        dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        dlg.setCurrentColor(old_qcolor)

        def apply_color(col: QColor):
            if not col.isValid():
                return
            hex_rgb = col.name(QColor.HexRgb)
            self.loomis_head.set_stroke_color(hex_rgb)
            self._sync_color_swatch()
            self.schedule_update()

        def confirm_color(col: QColor):
            apply_color(col)
            self._mark_preset_dirty()

        dlg.currentColorChanged.connect(apply_color)
        dlg.colorSelected.connect(confirm_color)

        def revert():
            self.loomis_head.set_stroke_color(old_hex)
            self._sync_color_swatch()
            instance.preset_dirty = old_dirty
            self._sync_preset_selection()
            self.schedule_update()

        dlg.rejected.connect(revert)
        dlg.exec_()

    def connect_ui(self):
        ui_path = os.path.join(os.path.dirname(__file__), "ui", "LoomisDocker.ui")
        self.ui: QWidget = uic.loadUi(ui_path)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.ui)
        self.setWidget(scroll)

        self._apply_limit_ranges()

        self.loomis_head.set_sidecut(self.ui.sideCutSlider.value() * 0.01)
        self.loomis_head.set_face_age(self.ui.faceAgeSlider.value() * 0.01)
        self.loomis_head.set_chin_width(self.ui.chinWidthSlider.value() * 0.01)
        self.loomis_head.set_front_line_stroke(self.ui.frontStrokeSlider.value())
        self.loomis_head.set_back_line_stroke(self.ui.backStrokeSlider.value())
        self.loomis_head.set_show_face(self.ui.showFace.isChecked())
        self.loomis_head.set_arrow(self.ui.showArrow.isChecked())
        self.loomis_head.set_silhouette(self.ui.showSilhouette.isChecked())
        self.loomis_head.set_side_rims(self.ui.showSideRims.isChecked())
        self.loomis_head.set_side_cross(self.ui.showSideCross.isChecked())

        host = self.ui.findChild(QWidget, "trackballHost")
        self.trackball = TrackballWidget(host)
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.trackball)

        self.trackball.orientation_changed.connect(
            lambda v: self.with_schedule_update(
                lambda: self.loomis_head.set_quaternion(v), preset_change=False
            )
        )

        self.ui.sideCutSlider.valueChanged.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_sidecut(v * 0.01))
        )
        self.ui.faceAgeSlider.valueChanged.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_face_age(v * 0.01))
        )
        self.ui.chinWidthSlider.valueChanged.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_chin_width(v * 0.01))
        )
        self.ui.showFace.toggled.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_show_face(v))
        )
        self.ui.frontStrokeSlider.valueChanged.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_front_line_stroke(v))
        )
        self.ui.backStrokeSlider.valueChanged.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_back_line_stroke(v))
        )

        self.ui.showArrow.toggled.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_arrow(v))
        )
        self.ui.showSilhouette.toggled.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_silhouette(v))
        )
        self.ui.showSideRims.toggled.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_side_rims(v))
        )
        self.ui.showSideCross.toggled.connect(
            lambda v: self.with_schedule_update(lambda: self.loomis_head.set_side_cross(v))
        )
        self.ui.strokeColorButton.clicked.connect(self.pick_stroke_color)
        self.ui.resetButton.clicked.connect(self.reset_view)
        self.ui.resetSettingsButton.clicked.connect(self.reset_settings)

        self.ui.headCombo.currentIndexChanged.connect(self.select_instance)
        self.ui.addHeadButton.clicked.connect(self.add_head_clicked)
        self.ui.duplicateHeadButton.clicked.connect(self.duplicate_head_clicked)
        self.ui.deleteHeadButton.clicked.connect(self.delete_head_clicked)

        self.ui.loadPresetButton.clicked.connect(self.load_preset)
        self.ui.savePresetButton.clicked.connect(self.save_preset)
        self.ui.deletePresetButton.clicked.connect(self.delete_preset)
        self.ui.presetCombo.currentIndexChanged.connect(self._update_preset_buttons)

        self._sync_color_swatch()

    # --- presets --------------------------------------------------------

    def refresh_preset_combo(self, selected=None):
        combo = self.ui.presetCombo
        current = selected if selected is not None else combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for name, _model, is_builtin in self.preset_store.all_presets():
            label = name if not is_builtin else "%s (built-in)" % name
            combo.addItem(label, name)
        if current is not None:
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)
        self._update_preset_buttons()

    def _update_preset_buttons(self, *_args):
        has_instance = self.active_instance() is not None
        name = self.ui.presetCombo.currentData()
        self.ui.presetCombo.setEnabled(has_instance)
        self.ui.loadPresetButton.setEnabled(has_instance and bool(name))
        self.ui.savePresetButton.setEnabled(has_instance)
        self.ui.deletePresetButton.setEnabled(
            has_instance and bool(name) and name not in builtin_names()
        )

    def _preset_config(self, name):
        if not name:
            return None
        return self.preset_store.get(name)

    def load_preset(self):
        instance = self.active_instance()
        if instance is None:
            return
        name = self.ui.presetCombo.currentData()
        if not name:
            return
        config = self._preset_config(name)
        if config is None:
            return
        self.loomis_head.apply_preset_dict(config)
        instance.preset_name = name
        instance.preset_dirty = False
        self.sync_ui_from_model()
        self._sync_preset_selection()
        self.schedule_update()

    def save_preset(self):
        instance = self.active_instance()
        if instance is None:
            return
        current = self.ui.presetCombo.currentData()
        suggestion = current if current and current not in builtin_names() else ""
        name, accepted = QInputDialog.getText(self, "Save preset", "Preset name:", text=suggestion)
        if not accepted:
            return
        try:
            saved = self.preset_store.save(name, self.loomis_head.to_config_dict())
        except ValueError as exc:
            QMessageBox.information(self, "Save preset", str(exc))
            return
        except OSError as exc:
            QMessageBox.warning(self, "Save preset", "Could not save preset:\n%s" % exc)
            return
        instance.preset_name = saved
        instance.preset_dirty = False
        self._remember_instance(instance)
        self.refresh_preset_combo(selected=saved)
        self._sync_preset_selection()

    def delete_preset(self):
        if self.active_instance() is None:
            return
        name = self.ui.presetCombo.currentData()
        if not name:
            return
        if name in builtin_names():
            QMessageBox.information(self, "Delete preset", "Built-in presets cannot be deleted.")
            return
        answer = QMessageBox.question(
            self,
            "Delete preset",
            "Delete preset “%s”?" % name,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.preset_store.delete(name)
        except OSError as exc:
            QMessageBox.warning(self, "Delete preset", "Could not delete preset:\n%s" % exc)
            return
        state = self.active_state()
        if state is not None:
            for instance in state.instances.values():
                if instance.preset_name == name:
                    instance.preset_name = None
                    instance.preset_dirty = False
                    self._remember_instance(instance)
        self.refresh_preset_combo()
        self._sync_preset_selection()

    def sync_ui_from_model(self):
        model = self.loomis_head
        widgets = [
            self.ui.sideCutSlider,
            self.ui.faceAgeSlider,
            self.ui.chinWidthSlider,
            self.ui.frontStrokeSlider,
            self.ui.backStrokeSlider,
            self.ui.showFace,
            self.ui.showArrow,
            self.ui.showSilhouette,
            self.ui.showSideRims,
            self.ui.showSideCross,
        ]
        for widget in widgets:
            widget.blockSignals(True)

        self.ui.sideCutSlider.setValue(round(model.side_cut * 100))
        self.ui.faceAgeSlider.setValue(round(model.face_age * 100))
        self.ui.chinWidthSlider.setValue(round(model.chin_width * 100))
        self.ui.frontStrokeSlider.setValue(round(model.front_line_stroke))
        self.ui.backStrokeSlider.setValue(round(model.back_line_stroke))
        self.ui.showFace.setChecked(model.show_face)
        self.ui.showArrow.setChecked(model.show_arrow)
        self.ui.showSilhouette.setChecked(model.show_silhouette)
        self.ui.showSideRims.setChecked(model.show_side_rims)
        self.ui.showSideCross.setChecked(model.show_side_cross)

        for widget in widgets:
            widget.blockSignals(False)

        self._sync_color_swatch()
        self.trackball.blockSignals(True)
        self.trackball.set_quaternion(model.q)
        self.trackball.blockSignals(False)
        self._sync_preset_selection()

    # --- drawing --------------------------------------------------------

    def flush_pending_draw(self):
        if self.update_scheduled:
            self.draw_timer.stop()
            self.draw_lines_with_vectors()

    def schedule_update(self):
        if self.doc is not None and not self._drawing:
            self._sync_active_reference()
        if self.active_instance() is None:
            self.update_scheduled = False
            return
        self._remember_instance()
        # Absorb any pending Select Shapes move before a redraw would snap the
        # layer back to the last known model position.
        if self.doc is not None and not self._drawing and not self.update_scheduled:
            self.sync_manual_position()
        if self.update_scheduled:
            return

        self.update_scheduled = True
        self.draw_timer.start()

    def _draw_pending(self):
        if self.update_scheduled:
            self.draw_lines_with_vectors()

    def sync_manual_position(self):
        """Mirror manual shape moves and scale gestures into the model."""
        if self.doc is not None and not self._drawing:
            self._sync_active_reference()
        instance = self.active_instance()
        if (
            not self.doc
            or instance is None
            or self.update_scheduled
            or self._drawing
        ):
            return

        delta, factor, current, changed = instance.placement_delta()
        instance.last_shape_states = current
        if not changed:
            if instance.needs_normalize:
                instance.stable_polls += 1
                if instance.stable_polls >= NORMALIZE_SETTLE_POLLS:
                    instance.needs_normalize = False
                    instance.stable_polls = 0
                    self.schedule_update()
            return

        instance.stable_polls = 0
        if factor is not None:
            instance.apply_scale_factor(factor)
            instance.needs_normalize = True
            self._remember_instance(instance)
            return

        if delta is None:
            return

        instance.model.set_position_x(instance.model.position_x + delta[0])
        instance.model.set_position_y(instance.model.position_y + delta[1])
        instance._refresh_placement_baseline()
        self._remember_instance(instance)

    def draw_lines_with_vectors(self, samples: int = LIVE_SAMPLES):
        started = timing.now()
        self.update_scheduled = False
        sync_started = timing.now()
        if self.doc is not None and not self._drawing:
            self._sync_active_reference()
        instance = self.active_instance()
        sync_seconds = timing.now() - sync_started
        if not self.doc or instance is None:
            return

        draw_started = timing.now()
        self._drawing = True
        try:
            phases = instance.redraw(self.doc, samples=samples)
        finally:
            self._drawing = False
        draw_seconds = timing.now() - draw_started
        frame_seconds = timing.now() - started

        if timing.enabled():
            timing.emit(timing.format_redraw(phases))
            timing.emit(
                "frame total=%.2fms sync=%.2f redraw=%.2f other=%.2f samples=%d"
                % (
                    timing.milliseconds(frame_seconds),
                    timing.milliseconds(sync_seconds),
                    timing.milliseconds(draw_seconds),
                    timing.milliseconds(frame_seconds - sync_seconds - draw_seconds),
                    samples,
                )
            )

    # --- actions --------------------------------------------------------

    def reset_view(self):
        # Reset rotation only; leave position and scale untouched.
        if self.active_instance() is None:
            return
        self.trackball.reset(emit=False)
        self.loomis_head.set_quaternion(q_identity())
        self.schedule_update()

    def reset_settings(self):
        """Restore the assigned preset's visual settings, leaving placement alone."""
        instance = self.active_instance()
        if instance is None:
            return
        config = self._preset_config(instance.preset_name) if instance.preset_name else None
        if config is None:
            instance.preset_name = None
            config = LoomisHead3D().to_config_dict()
        instance.model.apply_config_dict(config)
        instance.preset_dirty = False
        self.sync_ui_from_model()
        self.schedule_update()


class LoomisHeadPlugin(Extension):
    def __init__(self, parent):
        super().__init__(parent)

    def setup(self):
        Krita.instance().addDockWidgetFactory(
            DockWidgetFactory(
                "loomisMethodHeadGuide",
                DockWidgetFactoryBase.DockPosition.DockRight,
                LoomisProportionsDocker,
            )
        )

    def createActions(self, window):
        # The plugin is exposed as a docker from Settings -> Dockers.
        pass
