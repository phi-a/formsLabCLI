from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Group
from rich.table import Table
from rich.text import Text

import formslab.bridge as bridge
import formslab.console.style as style
from formslab.console.sessions.base import CLIResult


def _project_root() -> Path:
    """The FORMS tree the catalog describes -- wherever the library installed."""
    return bridge.forms_root()


def _catalog_path(root: Path) -> Path:
    out = Path(bridge.catalog().DEFAULT_CATALOG_PATH)
    if out.is_absolute():
        return out
    return (root / out).resolve()


def _load_catalog() -> Tuple[Dict[str, Any], Path]:
    build_catalog = bridge.catalog().build_catalog
    root = _project_root()
    path = _catalog_path(root)

    if not path.exists():
        build_catalog(project_root=root, output_path=path)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = None
    # A catalog written before FORMS #753 carries `zen_namespace`; rebuild it
    # rather than browse with no alias hints.
    if not isinstance(payload, dict) or "mission_namespace" not in payload:
        build_catalog(project_root=root, output_path=path)
        payload = json.loads(path.read_text(encoding="utf-8"))

    return payload, path


def _catalog_hints(payload: Dict[str, Any]) -> Dict[str, str]:
    hints = payload.get("mission_namespace", {}).get("symbol_hints", []) or []
    out: Dict[str, str] = {}
    for item in hints:
        alias = str(item.get("alias", "") or "").strip()
        target = str(item.get("symbol_root", "") or "").strip()
        if alias and target:
            out[alias] = target
    return out


# The dependency ranks, in canonical order, used to group package roots in
# the overview. The top rank `skills` holds the live agent capabilities (not persisted to the catalog
# file) -- see `_render_skills`.
_LAYER_ORDER = ["bricks", "infrastructure", "skills"]

_SKILLS_TOKENS = {"skills", "ai", "capabilities", "caps"}


def _packages_by_layer(payload: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Group the manifest's code nodes by dependency rank -> package -> count.

    The rank is each node's own legacy-named `layer` field; the
    package is the first import segment (e.g. the `infrastructure` rank holds
    the `core` and `runtime` packages). Mirrors how FORMS is organized."""
    by_layer: Dict[str, Dict[str, int]] = {}
    for node in payload.get("nodes", []) or []:
        symbol = str(node.get("symbol") or "")
        parts = symbol.split(".")
        if len(parts) < 2 or parts[0] != "forms":
            continue
        layer = str(node.get("layer") or "")
        pkg = parts[1]
        by_layer.setdefault(layer, {})
        by_layer[layer][pkg] = by_layer[layer].get(pkg, 0) + 1
    return by_layer


def _capabilities() -> List[Any]:
    """The live agent capabilities (the skills rank). Built provider-free; empty
    if the agent stack can't be imported."""
    return bridge.capabilities()


def _symbol_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Project the unified manifest's code nodes into the legacy row shape this
    browser walks (a dotted ``symbol`` tree). The persisted file is now the node
    manifest (schema 4.0); variable/page nodes carry no dotted symbol, so they
    are skipped, and ``symbol_kind`` (class/function/method) feeds the Kind
    column exactly as the old per-symbol ``kind`` did."""
    rows: List[Dict[str, Any]] = []
    for node in payload.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        symbol = str(node.get("symbol") or "")
        if "." not in symbol:
            continue
        rows.append({
            "symbol": symbol,
            "kind": node.get("symbol_kind") or node.get("node_kind") or "",
            "summary": node.get("summary", "") or "",
            "signature": node.get("signature", "") or "",
            "file": node.get("file", "") or "",
            "line": node.get("line"),
        })
    return rows


def _children_for(root_symbol: str, rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    prefix = root_symbol + "."
    children: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "") or "")
        if not symbol.startswith(prefix):
            continue
        remainder = symbol[len(prefix):]
        if not remainder:
            continue
        child = remainder.split(".", 1)[0]
        entry = children.get(child)
        if entry is None:
            entry = {
                "kinds": set(),
                "summary": str(row.get("summary", "") or ""),
            }
            children[child] = entry
        kind = str(row.get("kind", "") or "")
        if kind:
            entry["kinds"].add(kind)
        if not entry["summary"]:
            entry["summary"] = str(row.get("summary", "") or "")
    return children


def _symbol_exists(symbol: str, rows: List[Dict[str, Any]]) -> bool:
    exact = symbol
    prefix = symbol + "."
    for row in rows:
        current = str(row.get("symbol", "") or "")
        if current == exact or current.startswith(prefix):
            return True
    return False


def _find_exact_row(symbol: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for row in rows:
        if str(row.get("symbol", "") or "") == symbol:
            return row
    return None


def _resolve_path(path_tokens: List[str], hints: Dict[str, str], rows: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], str]:
    if not path_tokens:
        return None, "Missing path", ""

    query = ".".join(path_tokens)
    segments = [seg for seg in query.split(".") if seg]
    if not segments:
        return None, "Missing path", query

    first = segments[0]
    current: Optional[str]
    if first in hints:
        current = hints[first]
    elif _symbol_exists(query, rows):
        # A full dotted path was given (e.g. `forms.facades.coordinates`).
        return query, None, query
    elif _symbol_exists("forms." + first, rows):
        # A bare package name (e.g. `kernel`, `facades`, `core`) -> its root.
        current = "forms." + first
    else:
        return None, f"Unknown root '{first}'. Use `cmd` to list roots.", query

    for seg in segments[1:]:
        # Convenient bridge: forms.<alias> and satellite.<alias> jump to known roots.
        if current in (hints.get("forms"), hints.get("satellite")) and seg in hints:
            current = hints[seg]
            continue

        children = _children_for(current, rows)
        if not children:
            return None, f"'{seg}' not found under '{current}'.", query

        match_name = None
        for name in children.keys():
            if name.lower() == seg.lower():
                match_name = name
                break
        if match_name is None:
            return None, f"'{seg}' not found under '{current}'.", query

        candidate = current + "." + match_name
        if not _symbol_exists(candidate, rows):
            return None, f"'{match_name}' has no deeper API entries.", query
        current = candidate

    return current, None, query


def _render_help() -> CLIResult:
    lines = [
        "CMD Browser (FORMS dependency ranks and packages):",
        "  --tree                     List dependency ranks and their packages",
        "  --tree bricks              List the bricks package's modules",
        "  --tree bricks models       Browse a package down to its symbols",
        "  --tree skills              List the live agent capabilities",
        "  --tree coord               Alias jump (authoring shortcut)",
        "  --tree --rebuild           Rebuild the node manifest",
        "  --tree --help              Show help",
        "  (Legacy aliases: cmd / --cmd)",
    ]
    return CLIResult(content=Text("\n".join(lines), style=style.TEXT))


def _render_roots(payload: Dict[str, Any], catalog_path: Path) -> CLIResult:
    """List the dependency ranks and their navigable packages."""
    by_layer = _packages_by_layer(payload)
    hints = _catalog_hints(payload)

    table = Table(title="FORMS — by dependency rank", show_lines=False)
    table.add_column("Rank", style=style.HEADER, no_wrap=True)
    table.add_column("Packages (navigable roots)", style=style.INFO, overflow="fold")
    table.add_column("Nodes", style=style.NUMBER, justify="right")

    for layer in _LAYER_ORDER:
        if layer == "skills":
            # Capabilities are the live agent tools, not persisted to the file.
            table.add_row(
                "skills",
                "ai - live agent tools (--tree skills)",
                str(len(_capabilities())),
            )
            continue
        pkgs = by_layer.get(layer, {})
        if not pkgs:
            continue
        pkgs_text = ", ".join(f"{p} ({c})" for p, c in sorted(pkgs.items()))
        table.add_row(layer, pkgs_text, str(sum(pkgs.values())))

    meta = Text(
        f"Catalog: {catalog_path} | nodes: {int(payload.get('node_count', 0) or 0)}",
        style=style.DIM,
    )
    aliases = Text("Aliases (jump): " + ", ".join(sorted(hints)), style=style.DIM)
    tips = Text(
        "Drill: --tree <package> [child ...] | --tree skills | --tree --rebuild",
        style=style.DIM,
    )
    return CLIResult(content=Group(table, meta, aliases, tips))


def _render_skills() -> CLIResult:
    """The skills rank: live agent capabilities, grouped by domain."""
    caps = _capabilities()
    if not caps:
        return CLIResult(content=Text(
            "skills: no capabilities available (agent stack not importable).",
            style=style.ERROR,
        ))

    table = Table(title="skills rank — live agent capabilities", show_lines=False)
    table.add_column("Capability", style=style.HEADER)
    table.add_column("Domain", style=style.INFO, no_wrap=True)
    table.add_column("Kind", style=style.NUMBER, no_wrap=True)
    table.add_column("Summary", style=style.TEXT, overflow="fold")

    for cap in sorted(caps, key=lambda c: (c.domain, c.name)):
        summary = " ".join(str(cap.description or "").split())
        if len(summary) > 90:
            summary = summary[:89].rstrip() + "…"
        table.add_row(cap.name, cap.domain, cap.kind, summary)

    note = Text(
        f"{len(caps)} capabilities. These are the agent's verbs (forms.ai), "
        "exposed live as tools rather than persisted to the catalog file.",
        style=style.DIM,
    )
    return CLIResult(content=Group(table, note))


def _render_node(payload: Dict[str, Any], query_tokens: List[str]) -> CLIResult:
    hints = _catalog_hints(payload)
    rows = _symbol_rows(payload)
    resolved, error, query = _resolve_path(query_tokens, hints, rows)
    if error:
        return CLIResult(content=Text(error, style=style.ERROR))
    if resolved is None:
        return CLIResult(content=Text("No node resolved.", style=style.ERROR))

    children = _children_for(resolved, rows)
    exact = _find_exact_row(resolved, rows)

    header = Text(f"CMD: {query} -> {resolved}", style=style.HEADER)

    if not children:
        lines = [f"{resolved}"]
        if exact is not None:
            sig = str(exact.get("signature", "") or "")
            if sig:
                lines.append(f"signature: {sig}")
            summary = str(exact.get("summary", "") or "")
            if summary:
                lines.append(f"summary: {summary}")
            file = str(exact.get("file", "") or "")
            line = exact.get("line")
            if file and line:
                lines.append(f"source: {file}:{line}")
        body = Text("\n".join(lines), style=style.TEXT)
        return CLIResult(content=Group(header, body))

    table = Table(show_lines=False)
    table.add_column("Child", style=style.INFO)
    table.add_column("Kind", style=style.NUMBER, no_wrap=True)
    table.add_column("Summary", style=style.TEXT, overflow="fold")

    for child in sorted(children.keys(), key=lambda c: c.lower()):
        info = children[child]
        kinds = ", ".join(sorted(info["kinds"])) if info["kinds"] else ""
        summary = str(info.get("summary", "") or "")
        table.add_row(child, kinds, summary)

    tips = Text(
        f"Next: --tree {query} <child>   (or jump roots: --tree <alias>)",
        style=style.DIM,
    )
    return CLIResult(content=Group(header, table, tips))


def _rebuild_catalog() -> CLIResult:
    root = _project_root()
    out = _catalog_path(root)
    result = bridge.catalog().build_catalog(project_root=root, output_path=out)
    lines = [
        f"Catalog rebuilt: {result.get('path')}",
        f"nodes: {result.get('symbol_count', 0)}",
        f"files_scanned: {result.get('files_scanned', 0)}",
    ]
    scan_errors = result.get("scan_errors", []) or []
    if scan_errors:
        lines.append(f"scan_errors: {len(scan_errors)}")
    return CLIResult(content=Text("\n".join(lines), style=style.SUCCESS))


def handle_cmd_command(raw: str) -> CLIResult:
    parts = raw.strip().split()
    if not parts:
        return _render_help()

    args = parts[1:]  # skip cmd/--cmd token
    if not args:
        payload, catalog_path = _load_catalog()
        return _render_roots(payload, catalog_path)

    token = args[0].lower()
    if token in ("--help", "help", "-h"):
        return _render_help()
    if token in ("--rebuild", "rebuild", "refresh"):
        return _rebuild_catalog()
    # The skills rank (live agent capabilities) is not in the catalog file.
    if token in _SKILLS_TOKENS:
        return _render_skills()

    query_tokens: List[str] = []
    for tok in args:
        if tok.startswith("--"):
            break
        query_tokens.append(tok)

    payload, _ = _load_catalog()
    return _render_node(payload, query_tokens)
