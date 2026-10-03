# exposureutils.py
# Exposure time determination based on umbra duration for SLTA imaging
# 
# This module provides automatic exposure selection logic based on predicted
# umbra duration, with support for CLI overrides and lock-in during sequences.

from math import isnan

SpM = 60.0        # Seconds per minute. Held locally rather than imported from
                  # forms.bricks.constants: exposure selection is lab logic and
                  # must work on a console install without the library.

# Exposure thresholds (seconds) -> exposure time (seconds)
# Format: (min_duration, max_duration, exposure_time)
EXPOSURE_TIERS = [
    (0, 15*SpM, 5*SpM),   # < 15 min umbra -> 5 min exposure
    (15*SpM, 30*SpM, 10*SpM),    # 15-30 min umbra -> 10 min exposure  
    (30*SpM, float('inf'), 15*SpM),  # > 30 min umbra -> 15 min exposure
]


def selectExposure(umbraDuration):
    """
    Select exposure time based on umbra duration.
    
    Parameters
    ----------
    umbraDuration : float
        Predicted umbra duration in seconds
        
    Returns
    -------
    int
        Exposure time in seconds (300, 600, or 900)
    """
    if umbraDuration is None or isnan(umbraDuration):
        return 600  # Default to 10 min if unknown
    
    for minD, maxD, exposure in EXPOSURE_TIERS:
        if minD <= umbraDuration < maxD:
            return exposure
    
    return 600  # Fallback


class ExposureManager:
    """
    Manages exposure selection with lock-in during image sequences.
    
    Once an image sequence begins, the exposure is locked until complete.
    Supports CLI override that takes precedence over automatic selection.
    
    Usage:
        mgr = ExposureManager()
        
        # Each frame, update with current umbra duration
        mgr.update(umbraDuration=1200)  # 20 minutes predicted
        
        # Get exposure (locked during sequence)
        exp = mgr.get()
        
        # Lock when starting capture
        mgr.lock()
        
        # Release when capture complete
        mgr.release()
        
        # CLI override
        mgr.setOverride(900)  # Force 15 min
        mgr.clearOverride()
    """
    
    def __init__(self, defaultExposure=600):
        self._default = defaultExposure
        self._computed = defaultExposure
        self._override = None
        self._locked = False
        self._lockedValue = None
        self._umbraDuration = None
    
    @property
    def umbraDuration(self):
        """Current umbra duration (seconds)"""
        return self._umbraDuration
    
    @property
    def isLocked(self):
        """True if exposure is locked during an image sequence"""
        return self._locked
    
    @property
    def hasOverride(self):
        """True if CLI override is active"""
        return self._override is not None
    
    def update(self, umbraDuration):
        """
        Update umbra duration and recompute exposure (if not locked).
        
        Parameters
        ----------
        umbraDuration : float
            Predicted umbra duration in seconds
        """
        self._umbraDuration = umbraDuration
        
        if not self._locked:
            self._computed = selectExposure(umbraDuration)
    
    def get(self):
        """
        Get current exposure time.
        
        Priority:
        1. Locked value (during active sequence)
        2. CLI override
        3. Computed from umbra duration
        
        Returns
        -------
        int
            Exposure time in seconds
        """
        if self._locked and self._lockedValue is not None:
            return self._lockedValue
        
        if self._override is not None:
            return self._override
        
        return self._computed
    
    def lock(self):
        """
        Lock current exposure value for image sequence.
        Call this when starting a capture.
        """
        self._locked = True
        self._lockedValue = self.get()
        return self._lockedValue
    
    def release(self):
        """
        Release exposure lock after sequence complete.
        Call this when capture finishes.
        """
        self._locked = False
        self._lockedValue = None
    
    def setOverride(self, exposure):
        """
        Set CLI override exposure.
        
        Parameters
        ----------
        exposure : int
            Override exposure time in seconds
        """
        self._override = exposure
    
    def clearOverride(self):
        """Clear CLI override, return to automatic selection"""
        self._override = None
    
    def status(self):
        """
        Return status dictionary for debugging/logging.
        
        Returns
        -------
        dict
            Current state
        """
        return {
            'exposure': self.get(),
            'computed': self._computed,
            'override': self._override,
            'locked': self._locked,
            'lockedValue': self._lockedValue,
            'umbraDuration': self._umbraDuration,
        }
