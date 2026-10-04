"""CubeSat geometry builders and reusable body-fixed facet models.

The interactive 3-D views (``scene``, ``animate``, ...) are in ``scene3d``,
which needs plotly. It is not imported here, so the rest of the package
works without plotly:

    from formslab.orbit.geometry.cubesat.scene3d import scene, animate
"""

from .surfaces import (CubeSatGeometry, RealizedGeometry, RectFacet,   # noqa: F401
                       FacetNode, flip_facet, mount, rect_patch_grid)
from .builder import build_6u_double_deployable
from .inspect import (surface_by_normal, facet_labels,                 # noqa: F401
                      signed_axis_label, opposite_axis_label,
                      facet_role,
                      print_surface_summary, print_roles)
