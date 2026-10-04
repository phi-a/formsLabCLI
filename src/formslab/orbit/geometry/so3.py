"""SO(3) rotation representation.

Stored as a 3x3 matrix whose columns are body-frame basis vectors
expressed in the reference frame (LVLH).

    R.apply(v_body)  ->  v_lvlh
"""

import math
import numpy as np


def _hat(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def _quat_from_matrix(m):
    m = np.asarray(m, dtype=float)
    tr = float(np.trace(m))
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        q = np.array([
            0.25 * s,
            (m[2, 1] - m[1, 2]) / s,
            (m[0, 2] - m[2, 0]) / s,
            (m[1, 0] - m[0, 1]) / s,
        ])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        q = np.array([
            (m[2, 1] - m[1, 2]) / s,
            0.25 * s,
            (m[0, 1] + m[1, 0]) / s,
            (m[0, 2] + m[2, 0]) / s,
        ])
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        q = np.array([
            (m[0, 2] - m[2, 0]) / s,
            (m[0, 1] + m[1, 0]) / s,
            0.25 * s,
            (m[1, 2] + m[2, 1]) / s,
        ])
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        q = np.array([
            (m[1, 0] - m[0, 1]) / s,
            (m[0, 2] + m[2, 0]) / s,
            (m[1, 2] + m[2, 1]) / s,
            0.25 * s,
        ])
    return q / np.linalg.norm(q)


def _matrix_from_quat(q):
    w, x, y, z = np.asarray(q, dtype=float)
    ww, xx, yy, zz = w * w, x * x, y * y, z * z
    wx, wy, wz = w * x, w * y, w * z
    xy, xz, yz = x * y, x * z, y * z
    return np.array([
        [ww + xx - yy - zz, 2.0 * (xy - wz),     2.0 * (xz + wy)],
        [2.0 * (xy + wz),     ww - xx + yy - zz, 2.0 * (yz - wx)],
        [2.0 * (xz - wy),     2.0 * (yz + wx),   ww - xx - yy + zz],
    ])


class SO3:
    """Rotation in SO(3)."""
    __slots__ = ('m',)

    def __init__(self, m):
        self.m = np.asarray(m, dtype=float).reshape(3, 3)

    # -- constructors ------------------------------------------------------

    @staticmethod
    def identity():
        return SO3(np.eye(3))

    @staticmethod
    def Rx(a):
        """Rotation about X (velocity axis)."""
        c, s = math.cos(a), math.sin(a)
        return SO3([[1, 0, 0], [0, c, -s], [0, s, c]])

    @staticmethod
    def Ry(a):
        """Rotation about Y (orbit-normal axis)."""
        c, s = math.cos(a), math.sin(a)
        return SO3([[c, 0, s], [0, 1, 0], [-s, 0, c]])

    @staticmethod
    def Rz(a):
        """Rotation about Z (zenith axis)."""
        c, s = math.cos(a), math.sin(a)
        return SO3([[c, -s, 0], [s, c, 0], [0, 0, 1]])

    @staticmethod
    def from_triad(x, y, z):
        """From three orthonormal vectors (body axes in LVLH)."""
        return SO3(np.column_stack([_hat(x), _hat(y), _hat(z)]))

    @staticmethod
    def align(forward, ref):
        """Body +X -> forward, body +Z toward ref (Gram-Schmidt).

        +Y = Z x X completes the right-hand triad.

        NOTE: when ref is a purely radial direction (ZENITH or NADIR),
        +Y is always horizontal (T-W plane) — a mathematical identity,
        not a bug.  To make +Y the secondary axis use align_y().

        Parameters
        ----------
        forward : array-like (3,)  exact direction for body +X
        ref     : array-like (3,)  reference for body +Z (best-effort)
        """
        x = _hat(forward)
        r = np.asarray(ref, dtype=float)
        z = r - np.dot(r, x) * x
        n = np.linalg.norm(z)
        if n < 1e-12:
            perp = np.array([0., 1., 0.]) if abs(x[1]) < 0.9 else np.array([1., 0., 0.])
            z = perp - np.dot(perp, x) * x
        z = _hat(z)
        y = np.cross(z, x)
        return SO3(np.column_stack([x, y, z]))

    @staticmethod
    def align_y(forward, ref):
        """Body +X -> forward, body +Y toward ref (Gram-Schmidt).

        +Z = X x Y completes the right-hand triad.

        Use this when +Y (e.g. a radiator panel normal) should track a
        reference direction (e.g. nadir) rather than +Z.  Unlike align(),
        the resulting +Y will vary around the orbit when ref has a radial
        component.

        Parameters
        ----------
        forward : array-like (3,)  exact direction for body +X
        ref     : array-like (3,)  reference for body +Y (best-effort)
        """
        x = _hat(forward)
        r = np.asarray(ref, dtype=float)
        y = r - np.dot(r, x) * x
        n = np.linalg.norm(y)
        if n < 1e-12:
            perp = np.array([0., 0., 1.]) if abs(x[2]) < 0.9 else np.array([0., 1., 0.])
            y = perp - np.dot(perp, x) * x
        y = _hat(y)
        z = np.cross(x, y)
        return SO3(np.column_stack([x, y, z]))

    # -- operations --------------------------------------------------------

    def apply(self, v):
        """Rotate vector: body frame -> LVLH."""
        return self.m @ np.asarray(v, dtype=float)

    @staticmethod
    def from_quat(q):
        """Construct from a unit quaternion ``[w, x, y, z]``."""
        q = np.asarray(q, dtype=float)
        return SO3(_matrix_from_quat(q / np.linalg.norm(q)))

    def as_quat(self):
        """Return unit quaternion ``[w, x, y, z]``."""
        return _quat_from_matrix(self.m)

    def rotation_angle_to(self, other):
        """Principal angle [rad] of the relative rotation to ``other``."""
        rel = self.m.T @ other.m
        c = 0.5 * (float(np.trace(rel)) - 1.0)
        return math.acos(max(-1.0, min(1.0, c)))

    def slerp(self, other, fraction):
        """Shortest-path spherical interpolation to ``other``."""
        t = max(0.0, min(1.0, float(fraction)))
        q0 = self.as_quat()
        q1 = other.as_quat()
        dot = float(np.dot(q0, q1))
        if dot < 0.0:
            q1 = -q1
            dot = -dot
        dot = max(-1.0, min(1.0, dot))

        if dot > 0.9995:
            q = q0 + t * (q1 - q0)
            return SO3.from_quat(q / np.linalg.norm(q))

        theta = math.acos(dot)
        s = math.sin(theta)
        q = (
            math.sin((1.0 - t) * theta) * q0
            + math.sin(t * theta) * q1
        ) / s
        return SO3.from_quat(q)

    def __matmul__(self, other):
        if isinstance(other, SO3):
            return SO3(self.m @ other.m)
        return NotImplemented

    @property
    def T(self):
        """Inverse (transpose) rotation: LVLH -> body."""
        return SO3(self.m.T)

    def __repr__(self):
        return f"SO3(\n{self.m}\n)"
