"""Read a command line one key at a time.

`input()` hands back a whole line, so between keystrokes the console is blind:
it cannot notice the window was resized, and the only way to move the content
pane is to type a word and press Enter. Both of those are the same limitation,
and reading keys instead of lines removes both -- the frame gets a chance to
react on every key, and on every idle tick between keys.

Two pieces live here, and both are deliberately dumb:

    `LineBuffer`  the edited text and where the cursor sits in it
    a *reader*    turns keypresses into tokens, per platform

Neither draws anything and neither knows what a frame is; `app.py` owns the
policy of what a token means. That split is what makes the interesting parts
(the buffer edits, the escape-sequence table) testable without a terminal.

A reader is only offered for an interactive terminal. Anywhere else --- a pipe,
a capture, a platform without the shim --- `open_reader` returns None and the
console falls back to plain `input()`, keeping redirected runs unchanged.
"""

import os
import sys
import time


# ─── Tokens ────────────────────────────────────────────────
# Plain strings: they compare cheaply, print readably in a failing test, and a
# literal character never collides with them because every name is multi-char.
ENTER = "enter"
BACKSPACE = "backspace"
DELETE = "delete"
LEFT = "left"
RIGHT = "right"
HOME = "home"
END = "end"
PAGE_UP = "page-up"
PAGE_DOWN = "page-down"
LINE_UP = "line-up"
LINE_DOWN = "line-down"
INTERRUPT = "interrupt"
EOF = "eof"
IGNORE = "ignore"

EDIT_KEYS = frozenset({BACKSPACE, DELETE, LEFT, RIGHT, HOME, END})
SCROLL_KEYS = frozenset({PAGE_UP, PAGE_DOWN, LINE_UP, LINE_DOWN})


class LineBuffer:
    """The text being typed, and the cursor position inside it."""

    def __init__(self, text: str = ""):
        self.text = text
        self.pos = len(text)

    # `pos` is an index into `text`, so it may sit one past the last character.
    def insert(self, char: str) -> None:
        self.text = self.text[:self.pos] + char + self.text[self.pos:]
        self.pos += len(char)

    def apply(self, token: str) -> None:
        if token == BACKSPACE and self.pos:
            self.text = self.text[:self.pos - 1] + self.text[self.pos:]
            self.pos -= 1
        elif token == DELETE and self.pos < len(self.text):
            self.text = self.text[:self.pos] + self.text[self.pos + 1:]
        elif token == LEFT:
            self.pos = max(0, self.pos - 1)
        elif token == RIGHT:
            self.pos = min(len(self.text), self.pos + 1)
        elif token == HOME:
            self.pos = 0
        elif token == END:
            self.pos = len(self.text)

    @property
    def trailing(self) -> int:
        """Columns between the cursor and the end of the text."""
        return len(self.text) - self.pos


# ─── Windows ───────────────────────────────────────────────
# msvcrt reports an arrow or page key as a two-character sequence: a '\x00' or
# '\xe0' lead byte, then a scan code.
_WIN_LEAD = ("\x00", "\xe0")
_WIN_SPECIAL = {
    "H": LINE_UP,   "P": LINE_DOWN,
    "I": PAGE_UP,   "Q": PAGE_DOWN,
    "K": LEFT,      "M": RIGHT,
    "G": HOME,      "O": END,
    "S": DELETE,
}
_WIN_CHARS = {
    "\r": ENTER, "\n": ENTER,
    "\x08": BACKSPACE, "\x7f": BACKSPACE,
    "\x03": INTERRUPT, "\x04": EOF,
    "\x1b": IGNORE,
}


class _WindowsReader:
    """Poll the console with msvcrt; no mode switching needed."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def poll(self, timeout: float):
        import msvcrt

        deadline = time.monotonic() + timeout
        while True:
            if msvcrt.kbhit():
                char = msvcrt.getwch()
                if char in _WIN_LEAD:
                    return _WIN_SPECIAL.get(msvcrt.getwch(), IGNORE)
                return _WIN_CHARS.get(char, char)
            if time.monotonic() >= deadline:
                return None
            # Idle politely; the caller uses this gap to check for a resize.
            time.sleep(0.01)


# ─── POSIX ─────────────────────────────────────────
# A terminal reports a special key as an escape sequence: ESC, then "[" (CSI) or
# "O" (SS3, which some terminals use for arrows), then a body, then a final
# letter or "~".
#
# Parsing that means reading BYTES from the file descriptor, never characters
# from `sys.stdin`. A buffered stream answers `read(1)` by pulling up to 8 KiB
# off the descriptor in one go, so the tail of a sequence ends up in Python's
# buffer where `select()` -- which only ever looks at the descriptor -- cannot
# see it. The key would then do nothing when pressed and surface as literal
# "[A" text on the following keystroke. Reading the descriptor directly keeps
# the readiness check and the parser looking at the same place.

# The tail of a sequence, after the leading ESC "[" or ESC "O".
_POSIX_SEQUENCES = {
    "A": LINE_UP,   "B": LINE_DOWN,
    "C": RIGHT,     "D": LEFT,
    "H": HOME,      "F": END,
    "5~": PAGE_UP,  "6~": PAGE_DOWN,
    "3~": DELETE,   "1~": HOME,     "4~": END,
}
_POSIX_CHARS = {
    "\r": ENTER, "\n": ENTER,
    "\x7f": BACKSPACE, "\x08": BACKSPACE,
    "\x03": INTERRUPT, "\x04": EOF,
}

_ESC = b"\x1b"
_INTRODUCERS = (b"[", b"O")
_FINAL = b"~"

# How long to wait for the rest of a sequence that has already started. Long
# enough for the bytes to arrive together, short enough that a bare ESC keypress
# is not left hanging.
_SEQUENCE_GAP = 0.05

_READ_SIZE = 1024


def decode_csi(tail: str) -> str:
    """Map the body of an escape sequence to a token."""
    return _POSIX_SEQUENCES.get(tail, IGNORE)


def _utf8_width(lead: int) -> int:
    """How many bytes the UTF-8 character starting with this byte occupies."""
    if lead < 0x80:
        return 1
    if lead >= 0xF0:
        return 4
    if lead >= 0xE0:
        return 3
    if lead >= 0xC0:
        return 2
    return 1        # a stray continuation byte; consume it and move on


def parse_key(buf: bytes):
    """Take one token off the front of `buf`.

    Returns `(token, consumed)`. A `consumed` of 0 means these bytes are the
    start of something longer and the caller should read more before deciding.
    """
    if not buf:
        return None, 0

    if buf[0:1] != _ESC:
        width = _utf8_width(buf[0])
        if len(buf) < width:
            return None, 0          # a multi-byte character, still arriving
        char = buf[:width].decode("utf-8", "replace")
        return _POSIX_CHARS.get(char, char), width

    if len(buf) < 2:
        return None, 0              # bare ESC so far, or a sequence starting
    if buf[1:2] not in _INTRODUCERS:
        # A lone ESC, or Alt+key. Drop only the ESC so the key itself still
        # parses on the next pass.
        return IGNORE, 1

    for i in range(2, len(buf)):
        byte = buf[i:i + 1]
        if byte.isalpha() or byte == _FINAL:
            return decode_csi(buf[2:i + 1].decode("ascii", "replace")), i + 1
    return None, 0


class _PosixReader:
    """Read the tty in cbreak mode, a whole escape sequence at a time."""

    def __init__(self, stream):
        self._stream = stream
        self._fd = stream.fileno()
        self._saved = None
        self._pending = bytearray()
        self._eof = False

    def __enter__(self):
        import atexit
        import termios
        import tty

        self._saved = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)
        # Belt and braces: `__exit__` restores on every normal path including
        # exceptions, and this catches an interpreter shutdown that bypasses it.
        # Nothing can cover SIGKILL -- that leaves the shell needing `reset`.
        atexit.register(self.restore)
        return self

    def __exit__(self, *exc):
        self.restore()
        return False

    def restore(self):
        """Put the terminal back as it was. Idempotent -- two callers race for it."""
        if self._saved is None:
            return
        import termios

        saved, self._saved = self._saved, None
        try:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, saved)
        except Exception:
            pass

    def _fill(self, timeout: float) -> bool:
        """Wait up to `timeout` for bytes, then take everything available."""
        import select

        if not select.select([self._fd], [], [], timeout)[0]:
            return False
        chunk = os.read(self._fd, _READ_SIZE)
        if not chunk:
            self._eof = True
            return False
        self._pending += chunk
        return True

    def poll(self, timeout: float):
        if not self._pending and not self._fill(timeout):
            return EOF if self._eof else None

        while True:
            token, used = parse_key(bytes(self._pending))
            if used:
                del self._pending[:used]
                return token
            # A sequence has started but not finished; give the rest a moment.
            if not self._fill(_SEQUENCE_GAP):
                # Nothing more is coming. Drop a byte so an incomplete sequence
                # can never wedge the reader.
                del self._pending[:1]
                return IGNORE


def open_reader(stream=None):
    """A key reader for this terminal, or None if keys cannot be read here."""
    stream = stream or sys.stdin
    try:
        if not stream.isatty():
            return None
    except Exception:
        return None

    if os.name == "nt":
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            return None
        return _WindowsReader()

    try:
        import termios  # noqa: F401
        import tty  # noqa: F401

        stream.fileno()
    except Exception:
        return None
    return _PosixReader(stream)
