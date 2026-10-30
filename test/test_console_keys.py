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
