# trackball.py
import math

from PyQt5.QtCore import QRect, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPainterPath, QPalette, QPen
from PyQt5.QtWidgets import QToolButton, QWidget

from .euclid import Vector3

from .linalg import (
    mat3_mul_vec,
    q_axis_angle,
    q_identity,
    q_mul,
    q_normalize,
    q_to_mat3,
    q_to_yaw_pitch_roll,
)


class _ResetButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setToolTip("Reset rotation")
        self.setFixedSize(22, 22)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        palette = self.palette()
        scale = min(self.width(), self.height()) / 22.0
        if not self.isEnabled():
            color = palette.color(QPalette.Disabled, QPalette.ButtonText)
        elif self.isDown():
            color = palette.color(QPalette.Highlight).darker(120)
        elif self.underMouse():
            color = palette.color(QPalette.Highlight)
        else:
            color = QColor(palette.color(QPalette.ButtonText))
            color.setAlphaF(0.7)

        if self.isEnabled() and (self.underMouse() or self.isDown()):
            background = QColor(palette.color(QPalette.Mid))
            background.setAlphaF(0.28 if self.isDown() else 0.18)
            p.setPen(Qt.NoPen)
            p.setBrush(background)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0), 4.0, 4.0)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        radius = 0.36 * min(self.width(), self.height())
        rect = QRectF(cx - radius, cy - radius, 2.0 * radius, 2.0 * radius)
        start_angle = 120.0
        head_len = 4.8 * scale
        head_half = 2.0 * scale
        end_angle = 30.0
        span_angle = (end_angle - start_angle) % 360.0

        pen = QPen(color, 1.8 * scale)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        arc = QPainterPath()
        arc.arcMoveTo(rect, start_angle)
        arc.arcTo(rect, start_angle, span_angle)
        p.drawPath(arc)

        theta = math.radians(end_angle)
        base_x = cx + radius * math.cos(theta)
        base_y = cy - radius * math.sin(theta)
        ux = -math.sin(theta)
        uy = -math.cos(theta)
        nx, ny = -uy, ux
        tip_x = base_x + ux * head_len
        tip_y = base_y + uy * head_len
        head = QPainterPath()
        head.moveTo(tip_x, tip_y)
        head.lineTo(base_x + nx * head_half, base_y + ny * head_half)
        head.lineTo(base_x - nx * head_half, base_y - ny * head_half)
        head.closeSubpath()
        p.fillPath(head, color)


class TrackballWidget(QWidget):
    orientation_changed = pyqtSignal(object)  # emits Quaternion

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(100, 100)

        self.yaw = 0.0  # around world Y
        self.pitch = 0.0  # around world X
        self.roll = 0.0  # around view Z (via ring)
        self._q = q_identity()

        # interaction state
        self._dragging = False
        self._mode = None  # "turntable" | "roll"
        self._theta0 = 0.0  # start angle for roll
        self._roll0 = 0.0

        self.setMouseTracking(False)
        self.reset_button = _ResetButton(self)
        self._update_quaternion()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        button = getattr(self, "reset_button", None)
        if button is None:
            return
        size = button.size()
        button.move(self.width() - size.width() - 2, self.height() - size.height() - 2)
        button.raise_()

    def _pos_to_forward_vec(self, pos):
        cx, cy, r = self._center_radius()
        rx = max(r * 0.82, 1.0)
        x = (pos.x() - cx) / rx
        y = (cy - pos.y()) / rx  # Y up
        d2 = x * x + y * y
        if d2 <= 1.0:
            z = math.sqrt(max(0.0, 1.0 - d2))
        else:
            s = 1.0 / math.sqrt(d2)
            x, y, z = x * s, y * s, 0.0
        return [x, y, z]

    def set_quaternion(self, q):
        self._q = q_normalize(q)
        self.yaw, self.pitch, self.roll = q_to_yaw_pitch_roll(self._q)
        self.update()

    def _update_quaternion(self, emit=True):
        qy = q_axis_angle([0, 1, 0], self.yaw)
        qx = q_axis_angle([1, 0, 0], self.pitch)
        q_turn = q_mul(qy, qx)

        R_turn = q_to_mat3(q_turn)
        fwd_v = mat3_mul_vec(R_turn, Vector3(0.0, 0.0, 1.0))
        fwd = [fwd_v.x, fwd_v.y, fwd_v.z]

        q_roll = q_axis_angle(fwd, self.roll)
        self._q = q_normalize(q_mul(q_roll, q_turn))

        if emit:
            self.orientation_changed.emit(self._q)

        self.update()

    def _center_radius(self):
        w, h = self.width(), self.height()
        r = 0.5 * min(w, h) - 2.0
        return (w * 0.5, h * 0.5, r)

    def _angle_at(self, pos):
        cx, cy, _ = self._center_radius()
        x = pos.x() - cx
        y = cy - pos.y()  # up
        return math.atan2(y, x)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)

        cx, cy, r = self._center_radius()
        dx = e.x() - cx
        dy = e.y() - cy
        dist = math.hypot(dx, dy)

        ring_outer = r
        ring_inner = 0.82 * r

        if ring_inner <= dist <= ring_outer:
            self._mode = "roll"
            self._theta0 = self._angle_at(e.pos())
            self._roll0 = self.roll
        elif dist <= ring_inner:
            self._mode = "turntable"
        else:
            self._mode = None

        if self._mode:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if not self._dragging or not self._mode:
            return super().mouseMoveEvent(e)

        if self._mode == "turntable":
            v = self._pos_to_forward_vec(e.pos())
            self.yaw = math.atan2(float(v[0]), float(v[2]))  # right = +yaw
            self.pitch = -math.asin(max(-1.0, min(1.0, float(v[1]))))  # up = +pitch
            self._update_quaternion()

        elif self._mode == "roll":
            th = self._angle_at(e.pos())
            dth = th - self._theta0
            if dth > math.pi:
                dth -= 2 * math.pi
            elif dth < -math.pi:
                dth += 2 * math.pi
            self.roll = self._roll0 + dth
            self._update_quaternion()

        e.accept()

    def mouseReleaseEvent(self, e):
        if self._dragging and e.button() == Qt.LeftButton:
            self._dragging = False
            self._mode = None
            self.unsetCursor()
            e.accept()
        else:
            super().mouseReleaseEvent(e)

    def reset(self, emit=True):
        self.yaw = self.pitch = self.roll = 0.0
        self._update_quaternion(emit=emit)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy, r = self._center_radius()
        rect = QRect(int(cx - r), int(cy - r), int(2 * r), int(2 * r))

        ring_inner = 0.82 * r
        p.setPen(QPen(QColor(170, 170, 170), 2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(rect)
        if ring_inner > 0:
            th = self.roll
            x = cx + math.cos(th) * r
            y = cy - math.sin(th) * r
            p.setPen(QPen(QColor(120, 120, 120), 3))
            p.drawLine(int(x), int(y), int(cx + math.cos(th) * (r - 10)), int(cy - math.sin(th) * (r - 10)))

        inner = QRect(int(cx - ring_inner), int(cy - ring_inner), int(2 * ring_inner), int(2 * ring_inner))
        p.setPen(QPen(QColor(160, 160, 200), 2))
        p.setBrush(QBrush(QColor(235, 235, 255)))
        p.drawEllipse(inner)

        R = q_to_mat3(self._q)
        v = mat3_mul_vec(R, Vector3(0.0, 0.0, 1.0))
        tip = (cx + v.x * ring_inner, cy - v.y * ring_inner)
        p.setPen(QPen(QColor(100, 100, 210), 3))
        p.drawLine(int(cx), int(cy), int(tip[0]), int(tip[1]))
