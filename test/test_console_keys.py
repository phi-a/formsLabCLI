"""Key decoding and line editing for the console's own reader.

`app.read_command` polls for keys instead of calling `input()`, which is what
lets the frame notice a resize and move the content pane without Enter. The
parts worth testing are the ones that need no terminal: the token tables and the
edit buffer.
"""

import io

import pytest

from formslab.console import keys


def test_a_pipe_gets_no_reader():
    """Redirected input keeps the plain `input()` path, so captures are unchanged."""
    assert keys.open_reader(io.StringIO()) is None


def test_page_and_arrow_sequences_decode_to_scroll_tokens():
    assert keys.decode_csi("5~") == keys.PAGE_UP
    assert keys.decode_csi("6~") == keys.PAGE_DOWN
    assert keys.decode_csi("A") == keys.LINE_UP
    assert keys.decode_csi("B") == keys.LINE_DOWN


def test_an_unknown_sequence_is_ignored_rather_than_typed():
    """A stray escape must not end up as literal text in the command."""
    assert keys.decode_csi("99Z") == keys.IGNORE


def test_every_scroll_token_is_one_the_pager_handles():
    assert keys.SCROLL_KEYS == {keys.PAGE_UP, keys.PAGE_DOWN,
                                keys.LINE_UP, keys.LINE_DOWN}


def test_typing_inserts_at_the_cursor():
    buf = keys.LineBuffer()
    for char in "run tvac":
        buf.insert(char)

    assert buf.text == "run tvac"
    assert buf.pos == 8
    assert buf.trailing == 0


def test_backspace_removes_the_character_before_the_cursor():
    buf = keys.LineBuffer("run tvacc")
    buf.apply(keys.BACKSPACE)

    assert buf.text == "run tvac"


def test_editing_mid_line_leaves_the_tail_intact():
    buf = keys.LineBuffer("run tvac")
    buf.apply(keys.HOME)
    for char in "x ":
        buf.insert(char)

    assert buf.text == "x run tvac"
    assert buf.trailing == len("run tvac")


def test_delete_removes_forwards_and_arrows_clamp():
    buf = keys.LineBuffer("abc")
    buf.apply(keys.HOME)
    buf.apply(keys.DELETE)
    assert buf.text == "bc"

    for _ in range(5):
        buf.apply(keys.LEFT)
    assert buf.pos == 0
    for _ in range(5):
        buf.apply(keys.RIGHT)
    assert buf.pos == len(buf.text)


@pytest.mark.parametrize("token", sorted(keys.EDIT_KEYS))
def test_edit_keys_never_insert_themselves_as_text(token):
    """A token name is multi-character, so it must not reach `insert`."""
    buf = keys.LineBuffer("ab")
    buf.apply(token)

    assert token not in buf.text


# ─── The REPL's read loop ──────────────────────────────────
# `read_command` is where a token becomes an edit, a scroll or a repaint. It
# needs a terminal, so the reader and the console are stubbed and only the
# policy is exercised.

class _ScriptedReader:
    """Hands back a fixed list of tokens; None means an idle tick."""

    def __init__(self, tokens):
        self._tokens = list(tokens)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def poll(self, timeout):
        return self._tokens.pop(0) if self._tokens else keys.ENTER


class _TtyIO(io.StringIO):
    def isatty(self):
        return True


def _drive(monkeypatch, tokens, *, sizes=None, on_scroll=None):
    from rich.console import Console, ConsoleDimensions

    from formslab import app

    class _Console(Console):
        def __init__(self):
            super().__init__(file=_TtyIO(), width=80, height=24,
                             force_terminal=True, color_system=None)
            self.size_reads = 0

        @property
        def size(self):
            self.size_reads += 1
            if sizes:
                return ConsoleDimensions(*sizes[min(self.size_reads - 1, len(sizes) - 1)])
            return ConsoleDimensions(80, 24)

    monkeypatch.setattr(app, "console", _Console())
    monkeypatch.setattr(app, "_frame_is_available", lambda *a, **k: True)
    monkeypatch.setattr(app.keys, "open_reader", lambda *a, **k: _ScriptedReader(tokens))
    monkeypatch.setattr(app, "scroll_view", on_scroll or (lambda where: False))
    painted = []
    monkeypatch.setattr(app, "_paint", lambda: painted.append(1))
    return app.read_command("ctrl> "), painted


def test_read_command_assembles_typed_characters(monkeypatch):
    line, _ = _drive(monkeypatch, list("run tvac") + [keys.ENTER])

    assert line == "run tvac"


def test_read_command_applies_edits_before_submitting(monkeypatch):
    tokens = list("run tvacc") + [keys.BACKSPACE, keys.HOME, "x", keys.ENTER]

    line, _ = _drive(monkeypatch, tokens)

    assert line == "xrun tvac"


def test_scroll_keys_move_the_pane_and_never_reach_the_text(monkeypatch):
    """PgDn must page the window, not type itself into the command."""
    moved = []
    tokens = ["r", keys.PAGE_DOWN, keys.LINE_UP, "x", keys.ENTER]

    line, _ = _drive(monkeypatch, tokens,
                     on_scroll=lambda where: moved.append(where) or True)

    assert line == "rx"
    assert moved == [keys.PAGE_DOWN, keys.LINE_UP]


def test_an_idle_tick_repaints_only_when_the_window_changed(monkeypatch):
    """This is the whole of the resize handling: notice between keys, repaint."""
    # Sizes are consumed one per `console.size` read: the initial read, then one
    # per idle tick. The second tick is where the window changes.
    sizes = [(80, 24), (80, 24), (100, 40), (100, 40), (100, 40)]

    line, painted = _drive(monkeypatch, [None, None, keys.ENTER], sizes=sizes)

    assert line == ""
    assert len(painted) == 1


def test_ctrl_c_still_breaks_out(monkeypatch):
    with pytest.raises(KeyboardInterrupt):
        _drive(monkeypatch, ["a", keys.INTERRUPT])


# ─── Byte-level sequence parsing ───────────────────────────
# Built from ordinals rather than escapes, because that is what the parser
# actually sees coming off the file descriptor.
ESC = bytes([27])
CR = bytes([13])
DEL = bytes([127])


def test_the_posix_reader_binds_to_the_descriptor_not_the_stream():
    """Reading `sys.stdin` is exactly what broke escape sequences on Linux.

    A buffered stream answers `read(1)` by pulling up to 8 KiB off the
    descriptor, stranding the rest of a sequence in Python's buffer where
    `select()` cannot see it. The reader must hold the descriptor itself so the
    readiness check and the parser look at the same place.
    """
    class _Stream:
        def fileno(self):
            return 7

    assert keys._PosixReader(_Stream())._fd == 7


def test_an_arrow_arriving_as_one_chunk_is_one_token():
    assert keys.parse_key(ESC + b"[A") == (keys.LINE_UP, 3)


def test_ss3_arrows_decode_as_well_as_csi():
    """Some terminals send ESC O A for Up rather than ESC [ A."""
    assert keys.parse_key(ESC + b"OA") == (keys.LINE_UP, 3)


def test_page_keys_decode():
    assert keys.parse_key(ESC + b"[5~") == (keys.PAGE_UP, 4)
    assert keys.parse_key(ESC + b"[6~") == (keys.PAGE_DOWN, 4)


def test_a_partial_sequence_asks_for_more_instead_of_guessing():
    assert keys.parse_key(ESC) == (None, 0)
    assert keys.parse_key(ESC + b"[") == (None, 0)


def test_enter_and_backspace_decode():
    assert keys.parse_key(CR) == (keys.ENTER, 1)
    assert keys.parse_key(DEL) == (keys.BACKSPACE, 1)


def test_a_sequence_never_leaks_into_the_typed_line():
    """The regression: arrows used to surface as literal '[A' text.

    Drains a buffer the way the reader does, mixing keys and text in one chunk.
    """
    buf = bytearray(ESC + b"[A" + b"r" + ESC + b"[6~" + b"n")
    tokens = []
    while buf:
        token, used = keys.parse_key(bytes(buf))
        assert used, "parser stalled on a complete buffer"
        del buf[:used]
        tokens.append(token)

    assert tokens == [keys.LINE_UP, "r", keys.PAGE_DOWN, "n"]
    assert "[" not in tokens and "A" not in tokens


def test_multibyte_characters_are_decoded_whole():
    assert keys.parse_key("é".encode()) == ("é", 2)
    assert keys.parse_key("é".encode()[:1]) == (None, 0)


def test_an_unknown_sequence_is_consumed_not_typed():
    token, used = keys.parse_key(ESC + b"[99Z")
    assert token == keys.IGNORE
    assert used == 5
