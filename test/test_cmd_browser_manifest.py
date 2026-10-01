"""Regression guard: the console API browser reads the unified node manifest.

`console/cmd_browser.py` (the `--tree` browser) reads the persisted catalog file
directly. When that file moved from the 1.3 symbol list to the 4.0 node manifest,
the browser silently projected zero rows -- a break CI missed because `cli/`
(app tier) had no coverage. This pins the file-shape contract the browser
depends on so the drift cannot recur unnoticed.

The whole module needs the optional `[forms]` extra: it asserts the browser's
contract against a manifest FORMS actually built, so a stub payload would pin
nothing. Skipped on a bare lab install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip(
    "forms", reason="needs the optional [forms] extra: pip install -e .[forms]")

from formslab.console.cmd_browser import (            # noqa: E402
    _LAYER_ORDER,
    _capabilities,
    _catalog_hints,
    _packages_by_layer,
    _resolve_path,
    _render_help,
    _symbol_rows,
    handle_cmd_command,
)
from forms.core.catalog import build_base_manifest_payload   # noqa: E402

# The tree the manifest is built from is the FORMS source, not this repository.
# Before the extraction these were the same directory.
ROOT = Path(__import__("forms").__file__).resolve().parents[2]


def test_browser_projects_code_rows_from_manifest():
    payload = build_base_manifest_payload(project_root=ROOT)
    rows = _symbol_rows(payload)
    assert rows, "browser projected zero rows from the node manifest"
    # Rows carry a dotted symbol and the python kind (class/function/method).
    assert all("." in r["symbol"] for r in rows)
    assert any(r["kind"] in ("function", "method", "class") for r in rows)


def test_browser_resolves_a_known_alias_path():
    payload = build_base_manifest_payload(project_root=ROOT)
    rows = _symbol_rows(payload)
    hints = _catalog_hints(payload)
    resolved, error, _ = _resolve_path(["Coord"], hints, rows)
    assert error is None
    assert resolved == "forms.bricks.frames.coord.Coord"


def test_tree_groups_packages_by_layer():
    # The overview groups packages by their dependency rank.
    # The infrastructure rank holds both `core` and `runtime` (FORMS #753 renamed
    # `zen`); bricks holds `bricks`. FORMS #752 removed the flatsat subsystem.
    assert _LAYER_ORDER == ["bricks", "infrastructure", "skills"]
    payload = build_base_manifest_payload(project_root=ROOT)
    by_layer = _packages_by_layer(payload)
    assert "bricks" in by_layer["bricks"]
    assert {"core", "runtime"} <= set(by_layer["infrastructure"])
    assert "routines" not in by_layer
    assert by_layer["infrastructure"]["core"] == 105
    assert by_layer["infrastructure"]["runtime"] == 9
    assert "zen" not in by_layer["infrastructure"]
    assert sum(by_layer["infrastructure"].values()) == 114
    assert "flatsat" not in by_layer


def test_bare_package_is_a_navigable_root():
    # A bare package name resolves to its `forms.<pkg>` root and walks down.
    payload = build_base_manifest_payload(project_root=ROOT)
    rows = _symbol_rows(payload)
    hints = _catalog_hints(payload)
    assert _resolve_path(["bricks"], hints, rows)[0] == "forms.bricks"
    assert _resolve_path(["core"], hints, rows)[0] == "forms.core"
    assert _resolve_path(["bricks", "orbit", "live"], hints, rows)[0] == \
        "forms.bricks.orbit.live"


def test_skills_lists_capabilities():
    # The skills rank is the live agent capabilities (not in the catalog file).
    caps = _capabilities()
    names = {c.name for c in caps}
    assert names, "skills layer surfaced no capabilities"
    assert "set_orbit" in names  # a known orbit capability (sso is now a callable brick)
    # `--tree skills` routes to the capability view and renders without error.
    assert handle_cmd_command("cmd skills").content is not None


def test_root_overview_renders_current_layers():
    # `cmd` is the route used by `/tree` / `--tree` with no path.
    assert handle_cmd_command("cmd").content is not None


def test_help_uses_current_dependency_ranks_and_does_not_advertise_routines():
    help_text = _render_help().content.plain
    assert "dependency ranks" in help_text
    assert "--tree routines" not in help_text


if __name__ == "__main__":
    test_browser_projects_code_rows_from_manifest()
    test_browser_resolves_a_known_alias_path()
    test_tree_groups_packages_by_layer()
    test_bare_package_is_a_navigable_root()
    test_skills_lists_capabilities()
    print("ok")
