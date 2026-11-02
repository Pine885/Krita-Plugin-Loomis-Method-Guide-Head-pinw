import math
from collections.abc import Sequence

from . import limits
from .euclid import Vector2, Vector3
from .geom_polyline import (
    EPS,
    clip_to_side_band,
    path_str,
    split_by_plane_facing,
    split_front_back,
)
from .linalg import (
    Poly2,
    Poly3,
    Quaternion,
    linspace,
    normalize,
    q_identity,
    q_normalize,
)


def _as_float(value, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _as_bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _valid_color(value) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("#")
        and len(value) in (4, 7, 9)
        and all(ch in "0123456789abcdefABCDEF" for ch in value[1:])
    )


class LoomisHead3D:
    def __init__(self) -> None:
        self.radius: float = limits.RADIUS.default
        self.scale: float = limits.SCALE.default
        # Absolute document-pixel coordinates for the head's center.
        self.position_x: float = 0.0
        self.position_y: float = 0.0
        self.side_cut: float = limits.SIDE_CUT.default
        # These controls affect only the added jaw/chin/cheek guides.
        self.face_age: float = limits.FACE_AGE.default
        self.chin_width: float = limits.CHIN_WIDTH.default
        self.show_face: bool = True
        self.front_line_stroke: float = limits.FRONT_LINE_STROKE.default
        self.back_line_stroke: float = limits.BACK_LINE_STROKE.default
        self.show_arrow: bool = True
        self.show_silhouette: bool = True
        self.show_side_rims: bool = True
        self.show_side_cross: bool = True
        self.stroke_color: str = "#6A54E7"
        self.q: Quaternion = q_identity()

    def set_quaternion(self, q: Quaternion) -> None:
        self.q = q_normalize(q)

    def set_scale(self, scale: float) -> None:
        self.scale = _as_float(scale, self.scale, limits.SCALE.minimum, limits.SCALE.maximum)

    def set_position_x(self, position_x: float) -> None:
        self.position_x = float(position_x)

    def set_position_y(self, position_y: float) -> None:
        self.position_y = float(position_y)

    def set_sidecut(self, side_cut: float) -> None:
        self.side_cut = _as_float(
            side_cut, self.side_cut, limits.SIDE_CUT.minimum, limits.SIDE_CUT.maximum
        )

    def set_face_age(self, face_age: float) -> None:
        self.face_age = _as_float(
            face_age, self.face_age, limits.FACE_AGE.minimum, limits.FACE_AGE.maximum
        )

    def set_chin_width(self, chin_width: float) -> None:
        self.chin_width = _as_float(
            chin_width, self.chin_width, limits.CHIN_WIDTH.minimum, limits.CHIN_WIDTH.maximum
        )

    def set_show_face(self, enable: bool) -> None:
        self.show_face = bool(enable)

    def set_front_line_stroke(self, front_line_stroke: float) -> None:
        self.front_line_stroke = _as_float(
            front_line_stroke,
            self.front_line_stroke,
            limits.FRONT_LINE_STROKE.minimum,
            limits.FRONT_LINE_STROKE.maximum,
        )

    def set_back_line_stroke(self, back_line_stroke: float) -> None:
        self.back_line_stroke = _as_float(
            back_line_stroke,
            self.back_line_stroke,
            limits.BACK_LINE_STROKE.minimum,
            limits.BACK_LINE_STROKE.maximum,
        )

    def set_arrow(self, enable: bool) -> None:
        self.show_arrow = enable

    def set_silhouette(self, enable: bool) -> None:
        self.show_silhouette = enable

    def set_side_rims(self, enable: bool) -> None:
        self.show_side_rims = enable

    def set_side_cross(self, enable: bool) -> None:
        self.show_side_cross = enable

    def set_stroke_color(self, color: str) -> None:
        self.stroke_color = color

    # --- preset serialization ------------------------------------------

    def to_preset_dict(self) -> dict:
        """Return reusable head settings. Position, direction and size are excluded."""
        return {
            "radius": self.radius,
            "side_cut": self.side_cut,
            "face_age": self.face_age,
            "chin_width": self.chin_width,
            "front_line_stroke": self.front_line_stroke,
            "back_line_stroke": self.back_line_stroke,
            "stroke_color": self.stroke_color,
            "guides": {
                "face": self.show_face,
                "arrow": self.show_arrow,
                "silhouette": self.show_silhouette,
                "side_rims": self.show_side_rims,
                "side_cross": self.show_side_cross,
            },
            "quaternion": [self.q.w, self.q.x, self.q.y, self.q.z],
        }

    def to_config_dict(self) -> dict:
        """Return only reusable visual settings, without position, direction or size."""
        data = self.to_preset_dict()
        data.pop("quaternion", None)
        return data

    def apply_config_dict(self, data: dict) -> None:
        """Apply visual settings only, ignoring placement, direction and size."""
        if not isinstance(data, dict):
            return
        config = dict(data)
        config.pop("position", None)
        config.pop("position_x", None)
        config.pop("position_y", None)
        config.pop("quaternion", None)
        config.pop("scale", None)
        self.apply_preset_dict(config)

    def to_local_dict(self) -> dict:
        """Return the per-document placement for this head instance."""
        return {
            "position": [self.position_x, self.position_y],
            "quaternion": [self.q.w, self.q.x, self.q.y, self.q.z],
            "scale": self.scale,
        }

    def apply_local_dict(self, data: dict) -> None:
        """Apply per-document position, direction and size, ignoring invalid fields."""
        if not isinstance(data, dict):
            return

        scale = data.get("scale")
        if scale is not None:
            try:
                scale = float(scale)
            except (TypeError, ValueError):
                pass
            else:
                if math.isfinite(scale):
                    self.set_scale(scale)

        position = data.get("position")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            try:
                px, py = float(position[0]), float(position[1])
            except (TypeError, ValueError):
                pass
            else:
                if math.isfinite(px) and math.isfinite(py):
                    self.set_position_x(px)
                    self.set_position_y(py)

        quat = data.get("quaternion")
        if isinstance(quat, (list, tuple)) and len(quat) == 4:
            try:
                w, x, y, z = (float(value) for value in quat)
            except (TypeError, ValueError):
                return
            if not all(math.isfinite(value) for value in (w, x, y, z)):
                return
            candidate = Quaternion(w, x, y, z)
            if candidate.magnitude() > EPS:
                self.set_quaternion(candidate)

    def apply_preset_dict(self, data: dict) -> None:
        """Apply preset data, ignoring invalid or unknown fields."""
        if not isinstance(data, dict):
            return

        self.radius = _as_float(
            data.get("radius"), self.radius, limits.RADIUS.minimum, limits.RADIUS.maximum
        )
        self.side_cut = _as_float(
            data.get("side_cut"), self.side_cut, limits.SIDE_CUT.minimum, limits.SIDE_CUT.maximum
        )
        self.set_face_age(_as_float(data.get("face_age"), self.face_age))
        self.set_chin_width(_as_float(data.get("chin_width"), self.chin_width))
        self.front_line_stroke = _as_float(
            data.get("front_line_stroke"),
            self.front_line_stroke,
            limits.FRONT_LINE_STROKE.minimum,
            limits.FRONT_LINE_STROKE.maximum,
        )
        self.back_line_stroke = _as_float(
            data.get("back_line_stroke"),
            self.back_line_stroke,
            limits.BACK_LINE_STROKE.minimum,
            limits.BACK_LINE_STROKE.maximum,
        )

        if _valid_color(data.get("stroke_color")):
            self.stroke_color = data["stroke_color"]

        guides = data.get("guides")
        if isinstance(guides, dict):
            self.set_show_face(_as_bool(guides.get("face"), self.show_face))
            self.set_arrow(_as_bool(guides.get("arrow"), self.show_arrow))
            self.set_silhouette(_as_bool(guides.get("silhouette"), self.show_silhouette))
            self.set_side_rims(_as_bool(guides.get("side_rims"), self.show_side_rims))
            self.set_side_cross(_as_bool(guides.get("side_cross"), self.show_side_cross))

        quat = data.get("quaternion")
        if isinstance(quat, (list, tuple)) and len(quat) == 4:
            w, x, y, z = (_as_float(value, 0.0) for value in quat)
            candidate = Quaternion(w, x, y, z)
            if candidate.magnitude() > EPS:
                self.set_quaternion(candidate)

    def _basis_from_nnormal(self, n: Vector3 | Sequence[float]) -> tuple[Vector3, Vector3]:
        nn = n if isinstance(n, Vector3) else Vector3(*n)
        nn = normalize(nn)

        axes = [Vector3(1.0, 0.0, 0.0), Vector3(0.0, 1.0, 0.0), Vector3(0.0, 0.0, 1.0)]
        a = min(axes, key=lambda ax: abs(nn.dot(ax)))

        u = normalize(nn.cross(a))
        v = nn.cross(u)
        return u, v

    def _circle_on_plane(
        self,
        normal: Vector3 | Sequence[float],
        center: Sequence[float],
        radius: float,
        samples: int = 256,
    ) -> Poly3:
        u, v = self._basis_from_nnormal(normal)
        cx, cy, cz = center
        urx, ury, urz = u.x * radius, u.y * radius, u.z * radius
        vrx, vry, vrz = v.x * radius, v.y * radius, v.z * radius
        pts: Poly3 = []
        append = pts.append
        make = Vector3
        for t in linspace(0.0, 2.0 * math.pi, samples + 1, endpoint=True):
            c, s = math.cos(t), math.sin(t)
            append(make(cx + urx * c + vrx * s, cy + ury * c + vry * s, cz + urz * c + vrz * s))
        if len(pts) > 1:
            pts[-1] = pts[0]
        return pts

    def _to_camera(self, pts: Sequence[Vector3]) -> Poly3:
        if not pts:
            return []
        q = self.q
        w, x, y, z = q.w, q.x, q.y, q.z
        ww = w * w
        w2 = w * 2
        wx2 = w2 * x
        wy2 = w2 * y
        wz2 = w2 * z
        xx = x * x
        x2 = x * 2
        xy2 = x2 * y
        xz2 = x2 * z
        yy = y * y
        yz2 = 2 * y * z
        zz = z * z
        m00 = ww + xx - zz - yy
        m01 = xy2 - wz2
        m02 = xz2 + wy2
        m10 = xy2 + wz2
        m11 = yy + ww - zz - xx
        m12 = yz2 - wx2
        m20 = xz2 - wy2
        m21 = yz2 + wx2
        m22 = zz + ww - xx - yy
        make = pts[0].__class__
        out: Poly3 = []
        append = out.append
        for p in pts:
            vx, vy, vz = p.x, p.y, p.z
            append(
                make(
                    m00 * vx + m01 * vy + m02 * vz,
                    m10 * vx + m11 * vy + m12 * vz,
                    m20 * vx + m21 * vy + m22 * vz,
                )
            )
        return out

    def _to_screen(
        self,
        pts_cam: Sequence[Vector3],
        w: float,
        h: float,
    ) -> Poly2:
        s = min(w, h) * 0.3 * self.scale
        xy: Poly2 = []
        for p in pts_cam:
            x_s = p.x * s + self.position_x
            y_s = self.position_y - p.y * s
            xy.append(Vector2(x_s, y_s))
        return xy

    def _emit_segments(
        self,
        segments: list[Poly3],
        width: float,
        height: float,
        front_paths: list[str],
        back_paths: list[str],
        plane_normal_cam: Vector3 | None = None,
    ) -> None:
        for segment in segments:
            segment_camera = self._to_camera(segment)
            if plane_normal_cam is None:
                fsegs, bsegs = split_front_back(segment_camera)
            else:
                fsegs, bsegs = split_by_plane_facing(segment_camera, plane_normal_cam)
            for s in bsegs:
                back_paths.append(path_str(self._to_screen(s, width, height)))
            for s in fsegs:
                front_paths.append(path_str(self._to_screen(s, width, height)))

    def build_svg(self, width: float, height: float, dash_back: str | None = "5,6", samples: int = 256) -> str:
        r = self.radius
        d = limits.SIDE_CUT.clamp(float(self.side_cut)) * r
        rim_r = math.sqrt(max(r * r - d * d, EPS))

        nx = Vector3(1.0, 0.0, 0.0)
        ny = Vector3(0.0, 1.0, 0.0)

        n_view = Vector3(0.0, 0.0, 1.0)
        n_sil_head = self.q.conjugated() * n_view

        n_plus_cam = self.q * nx
        n_minus_cam = self.q * (-nx)

        curves: list[Poly3] = []

        if self.show_silhouette:
            curves.append(self._circle_on_plane(n_sil_head, [0.0, 0.0, 0.0], r, samples))

        curves.append(self._circle_on_plane(nx, [0.0, 0.0, 0.0], r, samples))  # centerline
        curves.append(self._circle_on_plane(ny, [0.0, 0.0, 0.0], r, samples))  # equator

        rims_plus: list[Poly3] = []
        rims_minus: list[Poly3] = []

        if self.show_side_rims:
            rims_plus.append(self._circle_on_plane(nx, [d, 0.0, 0.0], rim_r, samples))
            rims_minus.append(self._circle_on_plane(nx, [-d, 0.0, 0.0], rim_r, samples))

        crosses_plus: list[Poly3] = []
        crosses_minus: list[Poly3] = []

        if self.show_side_cross:
            crosses_plus.extend(
                [
                    [Vector3(d, -rim_r, 0.0), Vector3(d, rim_r, 0.0)],
                    [Vector3(d, 0.0, -rim_r), Vector3(d, 0.0, rim_r)],
                ]
            )
            crosses_minus.extend(
                [
                    [Vector3(-d, -rim_r, 0.0), Vector3(-d, rim_r, 0.0)],
                    [Vector3(-d, 0.0, -rim_r), Vector3(-d, 0.0, rim_r)],
                ]
            )

        # Jaw guide: merge the downward jaw segment and front extension into
        # one smooth cubic Bezier curve. The curve starts at the lower side-cross/
        # rim intersection, follows the 10-degree inward/downward direction, then
        # smoothly turns toward the front (+Z), with the extension's 20-degree
        # downward slope and 15-degree inward direction. Keep the two sides separate.
        jaw_curves: list[Poly3] = []
        jaw_front_endpoints: dict[float, Vector3] = {}
        # Child/adult slider shortens the added lower face toward the child end.
        # At 100 (adult), the original v0.2.4 proportions are unchanged.
        age = self.face_age
        jaw_length = 0.8 * (1.22 * r - rim_r) * (0.65 + 0.35 * age)
        jaw_angle = math.radians(10.0)
        front_extension_length = r  # keep the front reach tied to the original sphere
        front_down_angle = math.radians(12.0 + 8.0 * age)
        front_inward_angle = math.radians(15.0)
        for side in (-1.0, 1.0):
            p0 = Vector3(side * d, -rim_r, 0.0)
            p1 = Vector3(
                side * d - side * jaw_length * math.tan(jaw_angle),
                -rim_r - jaw_length,
                0.0,
            )
            p2 = Vector3(
                (p1.x - side * front_extension_length * math.tan(front_inward_angle)) * self.chin_width,
                p1.y - front_extension_length * math.tan(front_down_angle),
                p1.z + front_extension_length,
            )

            # Cubic Bezier control points keep the curve tangent to the original
            # first and second segment directions, removing the sharp corner at p1.
            c1 = Vector3(
                p0.x + (p1.x - p0.x) * 0.9,
                p0.y + (p1.y - p0.y) * 0.9,
                p0.z + (p1.z - p0.z) * 0.9,
            )
            c2 = Vector3(
                p2.x - (p2.x - p1.x) * 0.9,
                p2.y - (p2.y - p1.y) * 0.9,
                p2.z - (p2.z - p1.z) * 0.9,
            )
            curve: Poly3 = []
            for i in range(65):
                t = i / 64.0
                u = 1.0 - t
                curve.append(Vector3(
                    u*u*u*p0.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*p2.x,
                    u*u*u*p0.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*p2.y,
                    u*u*u*p0.z + 3*u*u*t*c1.z + 3*u*t*t*c2.z + t*t*t*p2.z,
                ))
            jaw_curves.append(curve)
            jaw_front_endpoints[side] = p2

        # Connect the mirrored jaw curves across the front of the chin.
        jaw_front_connector = [jaw_front_endpoints[-1.0], jaw_front_endpoints[1.0]]

        # Center front guide: connect the midpoint of the chin line to the
        # forward pole of the sphere, staying on the face's center plane.
        chin_midpoint = Vector3(
            (jaw_front_endpoints[-1.0].x + jaw_front_endpoints[1.0].x) * 0.5,
            (jaw_front_endpoints[-1.0].y + jaw_front_endpoints[1.0].y) * 0.5,
            (jaw_front_endpoints[-1.0].z + jaw_front_endpoints[1.0].z) * 0.5,
        )
        sphere_front_edge = Vector3(0.0, 0.0, r)
        front_center_guide = [sphere_front_edge, chin_midpoint]

        # Cheekbone guides: begin directly on the side rim, then follow one
        # broad downward/inward arc to the jaw/chin junction.
        cheekbone_curves: list[Poly3] = []
        for side in (-1.0, 1.0):
            # Start exactly where the forward end of the depth-wise side-cross
            # guide meets the side-rim circle.
            cheek_rim_touch = Vector3(side * d, 0.0, rim_r)
            cheek_end = jaw_front_endpoints[side]

            # One continuous arc from the rim directly to the chin junction.
            cheek_curve: Poly3 = [cheek_rim_touch]
            dx = cheek_end.x - cheek_rim_touch.x
            dy = cheek_end.y - cheek_rim_touch.y
            dz = cheek_end.z - cheek_rim_touch.z
            cheek_c1 = Vector3(
                cheek_rim_touch.x + 0.22 * dx,
                cheek_rim_touch.y + 0.30 * dy,
                cheek_rim_touch.z + 0.22 * dz,
            )
            cheek_c2 = Vector3(
                cheek_rim_touch.x + 0.78 * dx,
                cheek_rim_touch.y + 0.78 * dy,
                cheek_rim_touch.z + 0.78 * dz,
            )
            for i in range(1, 65):
                t = i / 64.0
                u = 1.0 - t
                cheek_curve.append(Vector3(
                    u*u*u*cheek_rim_touch.x + 3*u*u*t*cheek_c1.x + 3*u*t*t*cheek_c2.x + t*t*t*cheek_end.x,
                    u*u*u*cheek_rim_touch.y + 3*u*u*t*cheek_c1.y + 3*u*t*t*cheek_c2.y + t*t*t*cheek_end.y,
                    u*u*u*cheek_rim_touch.z + 3*u*u*t*cheek_c1.z + 3*u*t*t*cheek_c2.z + t*t*t*cheek_end.z,
                ))
            cheekbone_curves.append(cheek_curve)

        front_paths: list[str] = []
        back_paths: list[str] = []

        for c in curves:
            self._emit_segments(clip_to_side_band(c, d), width, height, front_paths, back_paths)
        if self.show_face:
            for jaw_curve in jaw_curves:
                self._emit_segments([jaw_curve], width, height, front_paths, back_paths)
            self._emit_segments([jaw_front_connector], width, height, front_paths, back_paths)
            self._emit_segments([front_center_guide], width, height, front_paths, back_paths)
            for cheekbone_curve in cheekbone_curves:
                self._emit_segments([cheekbone_curve], width, height, front_paths, back_paths)
        for rim in rims_plus:
            self._emit_segments([rim], width, height, front_paths, back_paths, n_plus_cam)
        for rim in rims_minus:
            self._emit_segments([rim], width, height, front_paths, back_paths, n_minus_cam)
        for seg in crosses_plus:
            self._emit_segments([seg], width, height, front_paths, back_paths, n_plus_cam)
        for seg in crosses_minus:
            self._emit_segments([seg], width, height, front_paths, back_paths, n_minus_cam)

        arrow_d = ""
        if self.show_arrow:
            base2 = self._to_screen(self._to_camera([Vector3(0.0, 0.0, 0.0)]), width, height)[0]
            tip2 = self._to_screen(self._to_camera([Vector3(0.0, 0.0, 1.15 * r)]), width, height)[0]
            dv: Vector2 = tip2 - base2
            L = dv.magnitude()
            if L > 1.0:
                u = dv / L
                n = Vector2(-u.y, u.x)
                head_len = 0.04 * min(width, height)
                head_wid = 0.55 * head_len
                pL = tip2 - u * head_len + n * head_wid
                pR = tip2 - u * head_len - n * head_wid
                arrow_d = (
                    f"M {base2.x:.3f},{base2.y:.3f} L {tip2.x:.3f},{tip2.y:.3f} "
                    f"M {tip2.x:.3f},{tip2.y:.3f} L {pL.x:.3f},{pL.y:.3f} "
                    f"M {tip2.x:.3f},{tip2.y:.3f} L {pR.x:.3f},{pR.y:.3f} "
                )

        if self.show_arrow and arrow_d:
            # Keep the arrow in the back-path shape: it uses the back-stroke
            # style and cannot be selected or moved separately from the guide.
            back_paths.append(arrow_d)

        dash_attr = f' stroke-dasharray="{dash_back}"' if dash_back else ""
        svg: list[str] = [
            '<svg xmlns="http://www.w3.org/2000/svg">',
            f'<g title="head" fill="none" stroke="{self.stroke_color}">',
        ]

        if back_paths:
            svg.append(f'<path d="{"".join(back_paths)}" stroke-width="{self.back_line_stroke:g}" stroke-linecap="round" stroke-linejoin="round"{dash_attr} opacity="0.6"/>')

        if front_paths:
            svg.append(f'<path d="{"".join(front_paths)}" stroke-width="{self.front_line_stroke:g}" stroke-linecap="round" stroke-linejoin="round"/>')

        svg.append("</g></svg>")
        return "".join(svg)
