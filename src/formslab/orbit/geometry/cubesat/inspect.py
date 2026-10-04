"""Inspection and query utilities for realized CubeSat geometry.

These helpers work with RealizedGeometry and RectFacet objects but do not
produce plots. Use geometry.CubeSat.plots for matplotlib visualizations.
"""

import numpy as np

from .surfaces import RealizedGeometry, _AXIS_MAP


def signed_axis_label(vector, tol=1e-9):
    """Return the body-axis label for a unit vector, e.g. '+Y', or a fallback string."""
    vector = np.asarray(vector, dtype=float)
    for label, axis in _AXIS_MAP:
        if np.allclose(vector, axis, atol=tol):
            return label
    return str(tuple(np.round(vector, 4).tolist()))


def opposite_axis_label(label):
    """Return the label for the axis opposite to *label*, e.g. '+Y' -> '-Y'."""
    if isinstance(label, str) and len(label) == 2 and label[0] in '+-' and label[1] in 'XYZ':
        return ('-' if label[0] == '+' else '+') + label[1]
    return f'-({label})'


def facet_labels(facet):
    """Return body-axis labels for the (u, v, normal) axes of *facet*."""
    frame = facet.frame_matrix
    return (
        signed_axis_label(frame[:, 0]),
        signed_axis_label(frame[:, 1]),
        signed_axis_label(frame[:, 2]),
    )


def facet_role(facet):
    """Return a compact body-role label derived from the realized normal."""
    normal_label = signed_axis_label(facet.normal)
    if 'solar_panel' in facet.tags:
        return f'body {normal_label} solar panel'
    if 'bus' in facet.tags:
        return f'body {normal_label} bus face'
    return f'body {normal_label} facet'


def surface_by_normal(realized, target_normal, *, tag=None, tol=1e-9):
    """Return the unique facet whose normal matches *target_normal*."""
    target = np.asarray(target_normal, dtype=float)
    target = target / np.linalg.norm(target)
    facets = realized.facets if tag is None else realized.by_tag(tag)
    matches = [f for f in facets if np.allclose(f.normal, target, atol=tol)]
    if len(matches) != 1:
        raise ValueError(
            f'expected exactly one facet with normal {tuple(target.tolist())}, '
            f'found {len(matches)}'
        )
    return matches[0]


def print_surface_summary(realized):
    """Print a compact table of all facets in *realized*."""
    header = (
        f"{'builder id':24s} {'body role':22s} {'center [m]':30s} "
        f"{'normal':8s} {'size [m]':18s} patches tags"
    )
    print(header)
    print('-' * len(header))
    for facet in realized.facets:
        center = tuple(np.round(facet.center, 4).tolist())
        normal = signed_axis_label(facet.normal)
        role = facet_role(facet)
        size = f"{facet.width:.4f} x {facet.height:.4f}"
        patches = (
            '1 x 1' if facet.patch_shape is None
            else f"{facet.patch_shape[0]} x {facet.patch_shape[1]}"
        )
        tags = ', '.join(facet.tags)
        print(
            f"{facet.name:24s} {role:22s} {str(center):30s} {normal:8s} "
            f"{size:18s} {patches:7s} {tags}"
        )


def print_roles(realized):
    """Print a table showing each facet's body-frame axis roles."""
    header = (
        f"{'builder id':24s} {'body role':22s} {'normal':8s} {'+u':8s} "
        f"{'+v':8s} {'center [m]':30s} tags"
    )
    print(header)
    print('-' * len(header))
    for facet in realized.facets:
        u_label, v_label, n_label = facet_labels(facet)
        center = tuple(np.round(facet.center, 4).tolist())
        role = facet_role(facet)
        tags = ', '.join(facet.tags)
        print(
            f"{facet.name:24s} {role:22s} {n_label:8s} {u_label:8s} {v_label:8s} "
            f"{str(center):30s} {tags}"
        )
