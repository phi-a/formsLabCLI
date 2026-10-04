"""Inertial-target visibility primitives and observation-window models."""

from .arcs import ArcWindow, CircularArc, intersect_arc_range, intersect_arcs  # noqa: F401
from .observation import (  # noqa: F401
    ObservationCheck,
    ObservationWindowFrame,
    ObservationWindowModel,
    observation_window,
)
from .segmentation import (  # noqa: F401
    VisibilityEvent,
    VisibilityFrame,
    VisibilitySegmentationModel,
    VisibilitySeries,
    detect_visibility_events,
    summarize_visibility,
)
from .target import (  # noqa: F401
    CYGNUSX1,
    GALACTIC_CENTER,
    OBSERVABLE,
    OCCULTED,
    PARTIAL,
    SUNLIT,
    Target,
    VisibilityGeometry,
    arc_intersection,
    arc_margin,
    clear_half_angle,
    open_sky_budget,
    target_beta_uc,
    visibility_geometry,
    visibility_state,
)
