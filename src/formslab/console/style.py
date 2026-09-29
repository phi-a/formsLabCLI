# formslab/console/style.py

try:
    from rich.style import Style
    from rich.console import Console
except ModuleNotFoundError:
    def Style(*args, **kwargs):  # type: ignore[override]
        return None

    class Console:  # type: ignore[override]
        def print(self, *args, **kwargs):
            print(*args)

# ─── Core Color Palette ────────────────────────────────────
# Semantic naming: colors describe their PURPOSE, not their hue
indigo = "#2D265C"
red = "#B7001E"
green = "#00A84F"
blue = "#0084C7"
orange = "#FF6E48"
magenta = "#941C94"
yellow = "#F2AE1C"
offwhite = "#F4F4F4"
volcanic = "#8E3D2F"
slate = "#6B7280"        # Dimmed/secondary text
cyan = "#06B6D4"         # Info/metadata


# ─── Semantic Styles ───────────────────────────────────────
# What each style MEANS in the interface
BG      = Style(color="black")
TEXT    = Style(color=offwhite)              # Default body text
DIM     = Style(color=slate, dim=True)       # Secondary info, hints, units
HEADER  = Style(color=blue, bold=True)       # Section headers, command names
ERROR   = Style(color=red, bold=True)        # Errors, critical alerts
WARNING = Style(color=yellow)                # Warnings, caution
SUCCESS = Style(color=green)                 # Successful operations (subtle)
INFO    = Style(color=cyan)                  # Metadata, context labels

# Data display
NUMBER  = Style(color=orange)                # Numeric values (V, A, K)
VALUE   = Style(color=offwhite, bold=True)   # Emphasized measurements
LABEL   = Style(color=slate)                 # Field labels (CH, Voltage:, etc)
UNIT    = Style(color=slate, dim=True)       # Units (V, A, K, Hz)

# Interactive elements
ACCENT1 = Style(color=indigo)                # Current context (see TAB_ACTIVE)
ACCENT2 = Style(color=volcanic)              # Confirmations, special states
PROMPT  = Style(color=blue)                  # Prompt text
COMMAND = Style(color=cyan)                  # Command echoes

# Device/channel state
STATE_ON  = Style(color=green)               # Device ON
STATE_OFF = Style(color=slate, dim=True)     # Device OFF
STATE_ERR = Style(color=red)                 # Device ERROR


# ─── Tab bar ───────────────────────────────────────────────
# Selection is carried by brightness, not hue. Two dark chips of different
# colour are near-indistinguishable at terminal luminance, so the active tab is
# filled bright with dark text and the rest stay dark with light text.
TAB_ACTIVE   = Style(color="black", bgcolor=blue, bold=True)
TAB_INACTIVE = Style(color=offwhite, bgcolor="grey15")

# Prompts are assembled as markup strings, so they need the hex -- NOT a Style.
# Interpolating `SomeStyle.color` yields the Color object's repr, which is not a
# parseable colour; Rich drops the whole tag and the text renders unstyled, or
# in the tab bar's case, in whatever the surrounding style was. That failure is
# invisible in review, so route markup through this instead.
PROMPT_MARKUP = f"bold {blue}"
# ─── Layout ────────────────────────────────────────────────
PANEL_BORDER  = slate                        # Subtle borders
PANEL_PADDING = (0, 1)                       # Minimal padding

# ─── Prompt ────────────────────────────────────────────────
PROMPT_SUFFIX = ">"
PROMPT_PREFIX = "│"                          # Separator for breadcrumbs

# ─── Console ───────────────────────────────────────────────
console = Console()
