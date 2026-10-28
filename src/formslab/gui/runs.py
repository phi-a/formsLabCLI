"""Recorded runs, for the plot viewer.

A run's CSV is written by the host's recorder: ``<UTC start>.csv``, header
``index,timestamp,plan,<name> [<unit>]...``, one file per run. Anything else in
the folder (older scripts' CSVs, protocol traces) is listed as unsupported
rather than guessed at.

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

_NAME = re.compile(r"^\d{8}T\d{6}Z$")
_HEAD = ["index", "timestamp", "plan"]
_COLUMN = re.compile(r"^(?P<name>.*?)(?: \[(?P<unit>[^\]]*)\])?$")
MAX_POINTS = 2000


def _head(path: Path) -> tuple[list[str], str] | None:
    """The recorder's header cells and the plan its first row names, or None for
    any other file."""
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            header, first = f.readline(65536), f.readline(65536)
    except OSError:
        return None
    if not header.endswith("\n"):
        return None                       # empty, or the header is still being written
    cells = next(csv.reader([header]), [])
    row = next(csv.reader([first]), []) if first.endswith("\n") else []
    return (cells, row[2] if len(row) > 2 else "") if cells[:3] == _HEAD else None


def column(cell: str) -> tuple[str, str]:
    """('TC01', 'K') from 'TC01 [K]'; ('PSU1_CH1_ON', '') from a bare name."""
    m = _COLUMN.match(cell)
    return m.group("name"), m.group("unit") or ""


def scan(dirs) -> dict:
    """{'runs': [...], 'unsupported': [...]} for every CSV in `dirs`, newest run first."""
    runs: list[dict] = []
    unsupported: list[str] = []
    seen: set[Path] = set()
    for d in dirs:
        for path in sorted(Path(d).glob("*.csv")):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            head = _head(path) if _NAME.match(path.stem) else None
            if head is None:
                unsupported.append(path.name)
                continue
            header, plan = head
            stat = path.stat()
            runs.append({
                "id": path.stem,
                "name": plan,
                "started": datetime.strptime(path.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp(),
                "bytes": stat.st_size,
                "modified": stat.st_mtime,
                "columns": [dict(zip(("name", "unit"), column(cell))) for cell in header[3:]],
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
    """{'t': [epoch s], 'series': {name: [value|None]}, 'units': {name: unit}, 'rows': n,
    'every_s': s} for `variables` (all when None). Rows are thinned evenly to
    `max_points`, always keeping the last. `every_s` is the gap between the last two
    rows: the recorder's cadence (`record every`), or None before the second row."""
    by_name = {c["name"]: c["unit"] for c in run["columns"]}
    names = [v for v in (variables if variables is not None else by_name) if v in by_name]
    times: list[float] = []
    values: dict[str, list] = {n: [] for n in names}
    rows = csv.reader(io.StringIO(_complete_lines(Path(run["_dir"]) / f"{run['id']}.csv")))
    header = next(rows, [])                          # read again: it may have widened since the scan
    where = {column(cell)[0]: i for i, cell in enumerate(header) if i >= 3}
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
            "units": {n: by_name[n] for n in names}, "rows": len(times),
            "every_s": times[-1] - times[-2] if len(times) > 1 else None}


def _thin(n: int, max_points: int) -> range | list[int]:
    if n <= max_points:
        return range(n)
    step = n / max_points
    keep = sorted({int(i * step) for i in range(max_points)} | {n - 1})
    return keep


def find(dirs, run_id: str) -> dict | None:
    """The run named `run_id` (from `scan`), with the folder it is in."""
    for d in dirs:
        for run in scan([d])["runs"]:
            if run["id"] == run_id:
                return {**run, "_dir": str(d)}
    return None
