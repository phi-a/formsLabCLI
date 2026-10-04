"""View-factor layer: geometric visibility between spacecraft facets and sources.

This package computes what each facet patch can see, expressed as
dimensionless geometric factors. It does not compute watts or temperatures.

Modules
-------
earthdisk   Earth-disk quadrature, face-coordinate transforms, directional masks
panel       Legacy panel-resolved rectangular geometry
occlusion   Spacecraft self-occlusion ray tests and group-view integration
propagator  Orbit-sweep propagators returning Loading
"""

from .earthdisk import (EarthDiskQuadrature, EarthDiskSamples,       # noqa: F401
                        FACE_LOCAL_FRAMES, AzimuthElevationMask,
                        face_coordinates, integrate_face_response)
from .panel import RectangularPanel, PanelLoadingProfile              # noqa: F401
from .occlusion import (spacecraft_occlusion_mask,                   # noqa: F401
                        integrate_surface_response,
                        hemisphere_group_view)
from .propagator import (earth_loading_propagate, EarthLoadingProfile,  # noqa: F401
                         panel_loading_propagate,
                         facet_loading_propagate, Loading)
from .sampling import hemisphere_directions                           # noqa: F401
from .serialize import save_profiles, load_profiles                   # noqa: F401
