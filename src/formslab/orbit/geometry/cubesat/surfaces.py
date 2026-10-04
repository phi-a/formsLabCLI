"""Minimal CubeSat geometry layer built from rectangular facets."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..so3 import SO3


BODY_FRAME_LABEL = "+X=velocity, +Y=orbit_normal, +Z=zenith"

_AXIS_MAP = (
    ('+X', np.array([1.0, 0.0, 0.0])),
    ('-X', np.array([-1.0, 0.0, 0.0])),
    ('+Y', np.array([0.0, 1.0, 0.0])),
    ('-Y', np.array([0.0, -1.0, 0.0])),
    ('+Z', np.array([0.0, 0.0, 1.0])),
    ('-Z', np.array([0.0, 0.0, -1.0])),
)
_AXIS_LOOKUP = {label: axis for label, axis in _AXIS_MAP}


def _as_vec3(v):
    arr = np.asarray(v, dtype=float).reshape(3)
    return arr


def _unit(v):
    arr = _as_vec3(v)
    n = np.linalg.norm(arr)
    if n <= 1e-15:
        raise ValueError("vector norm must be positive")
    return arr / n


def _as_rotation_matrix(rotation):
    if rotation is None:
        return np.eye(3)
    if hasattr(rotation, 'm'):
        matrix = np.asarray(rotation.m, dtype=float)
    else:
        matrix = np.asarray(rotation, dtype=float)
    if matrix.shape != (3, 3):
        raise ValueError("rotation must be a 3x3 matrix or an SO3-like object")
    return matrix


def _jsonify(value):
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(item) for item in value]
    if isinstance(value, np.ndarray):
        return _jsonify(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


def _axis_vector_from_label(label):
    if label not in _AXIS_LOOKUP:
        raise ValueError(f"axis label must be one of {tuple(_AXIS_LOOKUP)}")
    return _AXIS_LOOKUP[label].copy()


def _rotation_about_axis(axis, angle):
    axis = _unit(axis)
    x, y, z = axis
    c = math.cos(angle)
    s = math.sin(angle)
    one_c = 1.0 - c
    return np.array([
        [c + x * x * one_c, x * y * one_c - z * s, x * z * one_c + y * s],
        [y * x * one_c + z * s, c + y * y * one_c, y * z * one_c - x * s],
        [z * x * one_c - y * s, z * y * one_c + x * s, c + z * z * one_c],
    ])


def _rotate_point_about_line(point, origin, axis, angle):
    rot = _rotation_about_axis(axis, angle)
    return _as_vec3(origin) + rot @ (_as_vec3(point) - _as_vec3(origin))


def _canonical_perpendicular_axis(axis):
    axis = _unit(axis)
    for candidate in (
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    ):
        perp = candidate - np.dot(candidate, axis) * axis
        if np.linalg.norm(perp) > 1e-12:
            return _unit(perp)
    raise ValueError("failed to choose a perpendicular axis")


def _minimal_axis_alignment(source_axis, target_axis):
    source_axis = _unit(source_axis)
    target_axis = _unit(target_axis)
    dot = float(np.clip(np.dot(source_axis, target_axis), -1.0, 1.0))
    if dot >= 1.0 - 1e-12:
        return np.eye(3)
    if dot <= -1.0 + 1e-12:
        return _rotation_about_axis(_canonical_perpendicular_axis(source_axis), math.pi)

    cross = np.cross(source_axis, target_axis)
    angle = math.atan2(np.linalg.norm(cross), dot)
    return _rotation_about_axis(cross, angle)


def mount(geom_axis, body_axis, geom_axis2=None, body_axis2=None):
    """Return a rigid geometry-frame -> body-frame alignment rotation."""
    source_primary = _axis_vector_from_label(geom_axis)
    target_primary = _axis_vector_from_label(body_axis)

    if (geom_axis2 is None) != (body_axis2 is None):
        raise ValueError("geom_axis2 and body_axis2 must be provided together")

    if geom_axis2 is None:
        return SO3(_minimal_axis_alignment(source_primary, target_primary))

    source_secondary = _axis_vector_from_label(geom_axis2)
    target_secondary = _axis_vector_from_label(body_axis2)
    if abs(np.dot(source_primary, source_secondary)) > 1e-12:
        raise ValueError("geom_axis2 must be orthogonal to geom_axis")
    if abs(np.dot(target_primary, target_secondary)) > 1e-12:
        raise ValueError("body_axis2 must be orthogonal to body_axis")

    source_basis = np.column_stack([
        source_primary,
        source_secondary,
        np.cross(source_primary, source_secondary),
    ])
    target_basis = np.column_stack([
        target_primary,
        target_secondary,
        np.cross(target_primary, target_secondary),
    ])
    rotation = target_basis @ source_basis.T
    if not np.allclose(rotation @ source_primary, target_primary, atol=1e-12):
        raise ValueError("failed to align the requested primary axis pair")
    if not np.allclose(rotation @ source_secondary, target_secondary, atol=1e-12):
        raise ValueError("failed to align the requested secondary axis pair")
    return SO3(rotation)


def rect_patch_grid(width, height, nx, ny):
    """Return ``(xx, yy)`` meshgrid of equal-area patch centers, shape ``(ny, nx)``."""
    x = (np.arange(nx) + 0.5) * (width / nx) - 0.5 * width
    y = (np.arange(ny) + 0.5) * (height / ny) - 0.5 * height
    return np.meshgrid(x, y, indexing='xy')


def flip_facet(facet, *, name=None, tags=None):
    """Return the co-planar back face of a rectangular facet."""
    if not isinstance(facet, RectFacet):
        raise TypeError("facet must be a RectFacet instance")

    return RectFacet(
        name=facet.name + '_back' if name is None else str(name),
        center=facet.center.copy(),
        normal=-facet.normal,
        u_axis=facet.u_axis.copy(),
        width=facet.width,
        height=facet.height,
        two_sided=facet.two_sided,
        patch_shape=facet.patch_shape,
        tags=facet.tags if tags is None else tuple(tags),
    )


def _facet_to_dict(facet):
    return {
        'name': facet.name,
        'center': _jsonify(facet.center),
        'normal': _jsonify(facet.normal),
        'u_axis': _jsonify(facet.u_axis),
        'width': float(facet.width),
        'height': float(facet.height),
        'two_sided': bool(facet.two_sided),
        'patch_shape': None if facet.patch_shape is None else list(facet.patch_shape),
        'tags': list(facet.tags),
    }


def _facet_from_dict(data):
    return RectFacet(
        name=data['name'],
        center=data['center'],
        normal=data['normal'],
        u_axis=data['u_axis'],
        width=float(data['width']),
        height=float(data['height']),
        two_sided=bool(data.get('two_sided', False)),
        patch_shape=None if data.get('patch_shape') is None else tuple(data['patch_shape']),
        tags=tuple(data.get('tags', ())),
    )


def _compose_mount_metadata(metadata, rotation, offset):
    combined = dict(metadata)
    previous_rotation = _as_rotation_matrix(combined.get('mount_rotation'))
    previous_offset = (
        np.zeros(3, dtype=float)
        if combined.get('mount_offset') is None
        else _as_vec3(combined['mount_offset'])
    )
    combined['mount_rotation'] = _jsonify(rotation @ previous_rotation)
    combined['mount_offset'] = _jsonify(offset + rotation @ previous_offset)
    return combined


@dataclass(frozen=True)
class RectFacet:
    """Rectangular facet defined in an arbitrary parent frame."""
    name: str
    center: np.ndarray
    normal: np.ndarray
    u_axis: np.ndarray
    width: float
    height: float
    two_sided: bool = False
    patch_shape: tuple[int, int] | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self):
        if self.width <= 0.0 or self.height <= 0.0:
            raise ValueError("facet width and height must be positive")

        center = _as_vec3(self.center)
        normal = _unit(self.normal)
        u_axis = _as_vec3(self.u_axis)
        u_axis = u_axis - np.dot(u_axis, normal) * normal
        u_norm = np.linalg.norm(u_axis)
        if u_norm <= 1e-15:
            raise ValueError("u_axis must not be parallel to normal")
        u_axis = u_axis / u_norm

        if self.patch_shape is None:
            patch_shape = None
        else:
            nx, ny = self.patch_shape
            nx = int(nx)
            ny = int(ny)
            if nx <= 0 or ny <= 0:
                raise ValueError("patch_shape entries must be positive")
            patch_shape = (nx, ny)

        object.__setattr__(self, 'center', center)
        object.__setattr__(self, 'normal', normal)
        object.__setattr__(self, 'u_axis', u_axis)
        object.__setattr__(self, 'patch_shape', patch_shape)
        object.__setattr__(self, 'tags', tuple(self.tags))

    @property
    def v_axis(self):
        return np.cross(self.normal, self.u_axis)

    @property
    def frame_matrix(self):
        """Columns are `(u_axis, v_axis, normal)`."""
        return np.column_stack([self.u_axis, self.v_axis, self.normal])

    @property
    def area(self):
        return self.width * self.height

    def corners(self):
        du = 0.5 * self.width * self.u_axis
        dv = 0.5 * self.height * self.v_axis
        return np.array([
            self.center - du - dv,
            self.center + du - dv,
            self.center + du + dv,
            self.center - du + dv,
        ])

    def patch_centers(self):
        """Return patch-center coordinates with shape `(ny, nx, 3)`."""
        nx, ny = self.patch_shape if self.patch_shape is not None else (1, 1)
        xx, yy = rect_patch_grid(self.width, self.height, nx, ny)
        return (
            self.center[None, None, :]
            + xx[..., None] * self.u_axis[None, None, :]
            + yy[..., None] * self.v_axis[None, None, :]
        )

    def patch_normals(self):
        nx, ny = self.patch_shape if self.patch_shape is not None else (1, 1)
        return np.broadcast_to(self.normal, (ny, nx, 3))

    def patch_area(self):
        nx, ny = self.patch_shape if self.patch_shape is not None else (1, 1)
        return self.area / (nx * ny)

    def ray_intersection_parameter(self, origin, direction, eps=1e-12):
        origin = _as_vec3(origin)
        direction = _unit(direction)
        denom = float(np.dot(direction, self.normal))
        if abs(denom) <= eps:
            return None

        t_hit = float(np.dot(self.center - origin, self.normal) / denom)
        if t_hit <= eps:
            return None

        hit = origin + t_hit * direction
        rel = hit - self.center
        u = float(np.dot(rel, self.u_axis))
        v = float(np.dot(rel, self.v_axis))
        if abs(u) > 0.5 * self.width + eps or abs(v) > 0.5 * self.height + eps:
            return None
        return t_hit


@dataclass(frozen=True)
class FacetNode:
    """Facet plus optional hinge state, expressed in its parent frame."""
    facet: RectFacet
    parent: str | None = None
    hinge_origin: np.ndarray | None = None
    hinge_axis: np.ndarray | None = None
    state_key: str | None = None
    default_angle: float = 0.0

    def __post_init__(self):
        if (self.hinge_origin is None) != (self.hinge_axis is None):
            raise ValueError("hinge_origin and hinge_axis must be provided together")
        if self.hinge_origin is not None:
            object.__setattr__(self, 'hinge_origin', _as_vec3(self.hinge_origin))
            object.__setattr__(self, 'hinge_axis', _unit(self.hinge_axis))


@dataclass(frozen=True)
class RealizedGeometry:
    """Realized body-frame geometry built from a `CubeSatGeometry`."""
    facets: tuple[RectFacet, ...]
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'facets', tuple(self.facets))
        object.__setattr__(self, 'metadata', dict(self.metadata))

    def by_name(self, name):
        for facet in self.facets:
            if facet.name == name:
                return facet
        raise KeyError(name)

    def names(self):
        return tuple(facet.name for facet in self.facets)

    def by_tag(self, tag):
        return tuple(facet for facet in self.facets if tag in facet.tags)

    def to_json(self, path):
        payload = dict(_jsonify(self.metadata))
        payload['facets'] = [_facet_to_dict(facet) for facet in self.facets]

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding='utf-8')
        return path

    @classmethod
    def from_json(cls, path):
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        facets = tuple(_facet_from_dict(item) for item in payload.pop('facets'))
        return cls(facets=facets, metadata=payload)

    def mounted(self, *, rotation=None, offset=None):
        rot = _as_rotation_matrix(rotation)
        shift = np.zeros(3, dtype=float) if offset is None else _as_vec3(offset)
        if np.allclose(rot, np.eye(3)) and np.allclose(shift, 0.0):
            return self

        transformed = []
        for facet in self.facets:
            transformed.append(
                RectFacet(
                    name=facet.name,
                    center=shift + rot @ facet.center,
                    normal=rot @ facet.normal,
                    u_axis=rot @ facet.u_axis,
                    width=facet.width,
                    height=facet.height,
                    two_sided=facet.two_sided,
                    patch_shape=facet.patch_shape,
                    tags=facet.tags,
                )
            )
        metadata = _compose_mount_metadata(self.metadata, rot, shift)
        return RealizedGeometry(tuple(transformed), metadata=metadata)

    def first_intersection(self, origin, direction, *, exclude=()):
        blocked = set(exclude)
        best = None
        best_t = None
        for facet in self.facets:
            if facet.name in blocked:
                continue
            t_hit = facet.ray_intersection_parameter(origin, direction)
            if t_hit is None:
                continue
            if best is None or t_hit < best_t:
                best = facet
                best_t = t_hit
        if best is None:
            return None
        return best, best_t


@dataclass(frozen=True)
class CubeSatGeometry:
    """Hierarchical CubeSat geometry composed of rectangular facets."""
    nodes: tuple[FacetNode, ...]
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        nodes = tuple(self.nodes)
        names = [node.facet.name for node in nodes]
        if len(names) != len(set(names)):
            raise ValueError("facet names must be unique")
        name_set = set(names)
        for node in nodes:
            if node.parent is not None and node.parent not in name_set:
                raise ValueError(f"unknown parent facet {node.parent!r}")
        object.__setattr__(self, 'nodes', nodes)
        object.__setattr__(self, 'metadata', dict(self.metadata))

    def default_state(self):
        state = {}
        for node in self.nodes:
            if node.state_key is not None:
                state[node.state_key] = node.default_angle
        return state

    def realize(self, state=None, *, mount_rotation=None, mount_offset=None):
        """Realize all facets for the given mechanism state."""
        if state is None:
            state = {}
        state = {**self.default_state(), **state}
        mechanism_state = {
            node.state_key: float(state[node.state_key])
            for node in self.nodes
            if node.state_key is not None
        }
        node_map = {node.facet.name: node for node in self.nodes}
        cache = {}

        def resolve(name):
            if name in cache:
                return cache[name]

            node = node_map[name]
            if node.parent is None:
                base_rot = np.eye(3)
                base_origin = np.zeros(3, dtype=float)
            else:
                _, base_rot, base_origin = resolve(node.parent)

            center_local = node.facet.center
            normal_local = node.facet.normal
            u_local = node.facet.u_axis

            if node.hinge_axis is not None:
                angle = mechanism_state.get(node.state_key, node.default_angle)
                center_local = _rotate_point_about_line(
                    center_local, node.hinge_origin, node.hinge_axis, angle
                )
                rot_local = _rotation_about_axis(node.hinge_axis, angle)
                normal_local = rot_local @ normal_local
                u_local = rot_local @ u_local

            center_body = base_origin + base_rot @ center_local
            normal_body = base_rot @ normal_local
            u_body = base_rot @ u_local
            realized = RectFacet(
                name=node.facet.name,
                center=center_body,
                normal=normal_body,
                u_axis=u_body,
                width=node.facet.width,
                height=node.facet.height,
                two_sided=node.facet.two_sided,
                patch_shape=node.facet.patch_shape,
                tags=node.facet.tags,
            )
            cache[name] = (realized, realized.frame_matrix, realized.center)
            return cache[name]

        realized_facets = tuple(resolve(node.facet.name)[0] for node in self.nodes)
        realized = RealizedGeometry(
            realized_facets,
            metadata={
                'geometry_name': self.metadata.get('example'),
                'body_frame': BODY_FRAME_LABEL,
                'mechanism_state': _jsonify(mechanism_state),
                'mount_rotation': _jsonify(np.eye(3)),
                'mount_offset': _jsonify(np.zeros(3, dtype=float)),
                'builder_metadata': _jsonify(self.metadata),
            },
        )
        return realized.mounted(rotation=mount_rotation, offset=mount_offset)
