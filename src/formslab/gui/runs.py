"""Recorded runs, for the plot viewer.

A run's CSV is written by the host's recorder: ``<plan>_<UTC>.csv``, header
``index,timestamp,<name> [<unit>]...``. A variable that appears mid-run starts
the next part, ``<plan>_<UTC>_1.csv``, with a wider header; the parts are one
run. Anything else in the folder (older scripts' CSVs, protocol traces) is
listed as unsupported rather than guessed at.

Callers name a run by id, found here by scanning; a client-supplied string is
never turned into a path.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(r"^(?P<run>.+_(?P<stamp>\d{8}T\d{6}Z))(?:_(?P<part>\d+))?$")
_COLUMN = re.compile(r"^(?P<name>.*?)(?: \[(?P<unit>[^\]]*)\])?$")
MAX_POINTS = 2000


def _header(path: Path) -> list[str] | None:
    """The recorder's header cells, or None for any other file."""
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            line = f.readline(65536)
    except OSError:
        return None
    if not line.endswith("\n"):
        return None                       # empty, or the header is still being written
    cells = next(csv.reader([line]), [])
    return cells if cells[:2] == ["index", "timestamp"] else None


def column(cell: str) -> tuple[str, str]:
    """('TC01', 'K') from 'TC01 [K]'; ('PSU1_CH1_ON', '') from a bare name."""
    m = _COLUMN.match(cell)
    return m.group("name"), m.group("unit") or ""


def scan(dirs) -> dict:
    """{'runs': [...], 'unsupported': [...]} for every CSV in `dirs`, newest run first."""
    parts: dict[str, list[tuple[int, Path, list[str]]]] = {}
    unsupported: list[str] = []
    seen: set[Path] = set()
    for d in dirs:
        for path in sorted(Path(d).glob("*.csv")):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            m, header = _NAME.match(path.stem), _header(path)
            if not m or header is None:
                unsupported.append(path.name)
                continue
            parts.setdefault(m.group("run"), []).append((int(m.group("part") or 0), path, header))
    runs = []
    for run_id, items in parts.items():
        items.sort()
        columns: dict[str, str] = {}
        for _, _, header in items:
            for cell in header[2:]:
                name, unit = column(cell)
                columns.setdefault(name, unit)
        stamp = _NAME.match(items[0][1].stem).group("stamp")
        runs.append({
            "id": run_id,
            "name": run_id[: -len(stamp) - 1],
            "started": datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp(),
            "parts": [p.name for _, p, _ in items],
            "bytes": sum(p.stat().st_size for _, p, _ in items),
            "modified": max(p.stat().st_mtime for _, p, _ in items),
            "columns": [{"name": n, "unit": u} for n, u in columns.items()],
        })
    runs.sort(key=lambda r: r["started"], reverse=True)
    return {"runs": runs, "unsupported": sorted(unsupported)}


def _parse_time(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _number(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None          # a failed reading is nan: a gap, not a value


def _complete_lines(path: Path) -> str:
    """The file's text up to its last newline: the host appends a row while we
    read, and a half-written row must not be shown."""
    data = path.read_bytes()
    return data[: data.rfind(b"\n") + 1].decode("utf-8", errors="replace")


def series(run: dict, variables: list[str] | None = None, max_points: int = MAX_POINTS) -> dict:
    """{'t': [epoch s], 'series': {name: [value|None]}, 'units': {name: unit}, 'rows': n}
    for `variables` (all when None). Rows are thinned evenly to `max_points`,
    always keeping the last."""
    by_name = {c["name"]: c["unit"] for c in run["columns"]}
    names = [v for v in (variables if variables is not None else by_name) if v in by_name]
    times: list[float] = []
    values: dict[str, list] = {n: [] for n in names}
    base = Path(run["_dir"])
    for part in run["parts"]:
        rows = csv.reader(io.StringIO(_complete_lines(base / part)))
        header = next(rows, None)
        if not header or header[:2] != ["index", "timestamp"]:
            continue
        where = {column(cell)[0]: i for i, cell in enumerate(header) if i >= 2}
        for row in rows:
            if len(row) < 2 or (t := _parse_time(row[1])) is None:
                continue
            times.append(t)
            for n in names:
                i = where.get(n)
                values[n].append(_number(row[i]) if i is not None and i < len(row) else None)
    keep = _thin(len(times), max_points)
    return {"t": [times[i] for i in keep],
            "series": {n: [v[i] for i in keep] for n, v in values.items()},
            "units": {n: by_name[n] for n in names}, "rows": len(times)}


def _thin(n: int, max_points: int) -> range | list[int]:
    if n <= max_points:
        return range(n)
    step = n / max_points
    keep = sorted({int(i * step) for i in range(max_points)} | {n - 1})
    return keep


def find(dirs, run_id: str) -> dict | None:
    """The run named `run_id` (from `scan`), with the folder its parts are in."""
    for d in dirs:
        for run in scan([d])["runs"]:
            if run["id"] == run_id:
                return {**run, "_dir": str(d)}
    return None
