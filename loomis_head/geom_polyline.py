from collections.abc import Sequence

from .euclid import Vector2, Vector3
from .linalg import Poly3, Segments3

EPS = 1e-6


def _lerp3(p0: Vector3, p1: Vector3, t: float) -> Vector3:
    return p0 + (p1 - p0) * t


def path_str(xy: Sequence[Vector2]) -> str:
    if not xy:
        return ""

    parts = [f"M {xy[0].x:.3f},{xy[0].y:.3f}"]

    for i in range(1, len(xy)):
        parts.append(f"L {xy[i].x:.3f},{xy[i].y:.3f}")

    return " ".join(parts) + " "


def split_front_back(
    pts_cam: Sequence[Vector3],
    z_eps: float = EPS,
) -> tuple[Segments3, Segments3]:
    n = len(pts_cam)
    if n < 2:
        return [], []

    z = [0.0 if abs(p.z) < z_eps else p.z for p in pts_cam]

    front: Segments3 = []
    back: Segments3 = []
    buf: Poly3 = []
    is_front: bool | None = None

    def push(seg: Poly3, flag: bool | None) -> None:
        if flag is None:
            return
        if len(seg) >= 2:
            if flag:
                front.append(list(seg))
            else:
                back.append(list(seg))

    for i in range(n):
        pi, zi = pts_cam[i], z[i]
        if i == 0:
            buf = [pi]
            is_front = zi >= 0.0
            continue

        pj, zj = pts_cam[i - 1], z[i - 1]

        if zj == 0.0 and zi == 0.0:
            buf.append(pi)
            continue

        flip = (zj >= 0.0) != (zi >= 0.0)
        if flip:
            denom = zj - zi
            if abs(denom) < EPS:
                pc = pj
            else:
                t = zj / denom
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                pc = _lerp3(pj, pi, float(t))
            buf.append(pc)
            push(buf, is_front)
            buf = [pc, pi]
            is_front = zi >= 0.0
        else:
            buf.append(pi)

    push(buf, is_front)
    return front, back


def clip_to_side_band(poly_head: Sequence[Vector3], d: float) -> Segments3:
    segs: Segments3 = []
    n = len(poly_head)
    if n < 2:
        return segs

    def intersect_x(p0: Vector3, p1: Vector3, xb: float) -> Vector3 | None:
        dx = p1.x - p0.x
        if abs(dx) < EPS:
            return None
        t = (xb - p0.x) / dx
        if 0.0 <= t <= 1.0:
            return _lerp3(p0, p1, float(t))
        return None

    def inside_x(p: Vector3) -> bool:
        return abs(p.x) <= d

    cur: Poly3 = []
    p_prev = poly_head[0]
    inside_prev = inside_x(p_prev)
    if inside_prev:
        cur = [p_prev]

    for i in range(1, n):
        p = poly_head[i]
        inside = inside_x(p)

        if inside_prev and inside:
            cur.append(p)

        elif inside_prev and not inside:
            xb = d if p.x > d else -d
            pc = intersect_x(p_prev, p, xb)
            if pc is not None:
                cur.append(pc)
            if cur:
                segs.append(cur)
            cur = []

        elif not inside_prev and inside:
            xb = d if p_prev.x > d else -d
            pc = intersect_x(p_prev, p, xb)
            cur = ([pc] if pc is not None else []) + [p]

        else:
            crosses = (p_prev.x < -d and p.x > d) or (p_prev.x > d and p.x < -d)
            if crosses:
                pa = intersect_x(p_prev, p, -d)
                pb = intersect_x(p_prev, p, d)
                if pa is not None and pb is not None:
                    segs.append([pa, pb])

        p_prev = p
        inside_prev = inside

    if cur:
        segs.append(cur)
    return segs


def split_by_plane_facing(
    pts_cam: Sequence[Vector3],
    plane_normal_cam: Vector3,
) -> tuple[Segments3, Segments3]:
    nz = float(plane_normal_cam.z)
    if nz > EPS:
        return [list(pts_cam)], []
    if nz < -EPS:
        return [], [list(pts_cam)]
    return split_front_back(list(pts_cam))
