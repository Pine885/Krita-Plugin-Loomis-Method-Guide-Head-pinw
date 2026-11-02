import math
from collections.abc import Sequence
from typing import TypeAlias

from .euclid import Matrix4, Quaternion, Vector2, Vector3

Poly2: TypeAlias = list[Vector2]
Poly3: TypeAlias = list[Vector3]
Segments3: TypeAlias = list[Poly3]
Mat3: TypeAlias = list[list[float]]


def normalize(v: Vector3) -> Vector3:
    n = v.magnitude()
    return v if n == 0.0 else v / n


def q_identity() -> Quaternion:
    return Quaternion(1.0, 0.0, 0.0, 0.0)


def q_normalize(q: Quaternion) -> Quaternion:
    return q.normalized()


def q_mul(b: Quaternion, a: Quaternion) -> Quaternion:
    return b * a


def q_axis_angle(axis_xyz: Vector3 | Sequence[float], angle_rad: float) -> Quaternion:
    if isinstance(axis_xyz, Vector3):
        ax = normalize(axis_xyz)
    else:
        ax = normalize(Vector3(*axis_xyz))
    return Quaternion.new_rotate_axis(angle_rad, ax)


def q_to_mat3(q: Quaternion) -> Mat3:
    m4: Matrix4 = q.get_matrix()
    r0 = [m4.a, m4.b, m4.c]
    r1 = [m4.e, m4.f, m4.g]
    r2 = [m4.i, m4.j, m4.k]
    return [r0, r1, r2]


def mat3_mul_vec(m: Mat3, v: Vector3) -> Vector3:
    return Vector3(
        m[0][0] * v.x + m[0][1] * v.y + m[0][2] * v.z,
        m[1][0] * v.x + m[1][1] * v.y + m[1][2] * v.z,
        m[2][0] * v.x + m[2][1] * v.y + m[2][2] * v.z,
    )


def q_to_yaw_pitch_roll(q: Quaternion) -> tuple[float, float, float]:
    """Split a normalized orientation into the trackball's turntable angles.

    The widget builds ``q = roll * yaw * pitch``. The forward axis is invariant
    under the final roll, so yaw and pitch come from it and roll is the signed
    angle between the unrolled and final right vectors about that axis.
    """
    q = q_normalize(q)
    forward = mat3_mul_vec(q_to_mat3(q), Vector3(0.0, 0.0, 1.0))
    yaw = math.atan2(forward.x, forward.z)
    pitch = -math.asin(max(-1.0, min(1.0, forward.y)))

    turn = q_mul(q_axis_angle([0.0, 1.0, 0.0], yaw), q_axis_angle([1.0, 0.0, 0.0], pitch))
    right_turn = mat3_mul_vec(q_to_mat3(turn), Vector3(1.0, 0.0, 0.0))
    right_final = mat3_mul_vec(q_to_mat3(q), Vector3(1.0, 0.0, 0.0))

    right_turn = right_turn - forward * right_turn.dot(forward)
    right_final = right_final - forward * right_final.dot(forward)
    if right_turn.magnitude() <= 1e-9 or right_final.magnitude() <= 1e-9:
        return yaw, pitch, 0.0

    right_turn = right_turn.normalized()
    right_final = right_final.normalized()
    roll = math.atan2(
        forward.dot(right_turn.cross(right_final)),
        right_turn.dot(right_final),
    )
    return yaw, pitch, roll


def linspace(start: float, stop: float, n: int, endpoint: bool = False) -> list[float]:
    if n <= 0:
        return []
    if n == 1:
        return [start]
    if endpoint:
        step = (stop - start) / (n - 1)
        return [start + i * step for i in range(n)]
    step = (stop - start) / n
    return [start + i * step for i in range(n)]
