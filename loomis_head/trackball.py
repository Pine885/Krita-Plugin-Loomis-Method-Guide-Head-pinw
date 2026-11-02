# trackball.py
import math

from PyQt5.QtCore import QRect, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

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
        self._update_quaternion()

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
