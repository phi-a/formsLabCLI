"""Recorded runs, for the plot viewer.

A run is written by the host's recorder: ``<plan>_<UTC start>.jsonl``, one
object per row, ``index, timestamp, plan`` then ``"<name> [<unit>]": value``, one
file per run. A variable only ever joins, so the last row names every column.
Anything else in the folder (older layouts, scripts' logs, protocol traces) is
listed as unsupported rather than guessed at.

Callers name a run by id, found here by scanning; a client-supplied string is
never turned into a path.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*_(?P<stamp>\d{8}T\d{6}Z)$")
_HEAD = ["index", "timestamp", "plan"]
_COLUMN = re.compile(r"^(?P<name>.*?)(?: \[(?P<unit>[^\]]*)\])?$")
_TAIL = 65536
MAX_POINTS = 2000


def _row(line: bytes) -> dict | None:
    """One recorder row, or None for anything else."""
    try:
        row = json.loads(line)
    except ValueError:
        return None
    return row if isinstance(row, dict) and list(row)[:3] == _HEAD else None


def _head(path: Path) -> tuple[list[str], str] | None:
    """The recorder's columns (from the last complete row) and the plan its first
    row names, or None for any other file."""
    try:
        with path.open("rb") as f:
            first = f.readline(_TAIL)
            f.seek(max(0, f.seek(0, 2) - _TAIL))
            tail = f.read()
    except OSError:
        return None
    if not first.endswith(b"\n") or (row := _row(first)) is None:
        return None                       # empty, the first row still being written, or not ours
    last = _row(tail[: tail.rfind(b"\n")].rsplit(b"\n", 1)[-1]) or row
    return list(last)[3:], str(row["plan"])


def column(cell: str) -> tuple[str, str]:
    """('TC01', 'K') from 'TC01 [K]'; ('PSU1_CH1_ON', '') from a bare name."""
    m = _COLUMN.match(cell)
    return m.group("name"), m.group("unit") or ""


def scan(dirs) -> dict:
    """{'runs': [...], 'unsupported': [...]} for every CSV and JSONL in `dirs`, newest
    run first."""
    runs: list[dict] = []
    unsupported: list[str] = []
    seen: set[Path] = set()
    for d in dirs:
        for path in sorted([*Path(d).glob("*.csv"), *Path(d).glob("*.jsonl")]):
            if path.resolve() in seen:
                continue
            seen.add(path.resolve())
            name = _NAME.match(path.stem) if path.suffix == ".jsonl" else None
            head = _head(path) if name else None
            if head is None:
                unsupported.append(path.name)
                continue
            header, plan = head
            stat = path.stat()
            runs.append({
                "id": path.stem,
                "name": plan,
                "started": datetime.strptime(name["stamp"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp(),
                "bytes": stat.st_size,
                "modified": stat.st_mtime,
                "columns": [dict(zip(("name", "unit"), column(cell))) for cell in header],
            })
    runs.sort(key=lambda r: r["started"], reverse=True)
    return {"runs": runs, "unsupported": sorted(unsupported)}


def _parse_time(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _number(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None          # a failed reading is null: a gap, not a value


def _complete_lines(path: Path) -> list[bytes]:
    """The file's lines up to its last newline: the host appends a row while we
    read, and a half-written row must not be shown."""
    data = path.read_bytes()
    return data[: data.rfind(b"\n")].split(b"\n") if b"\n" in data else []


def series(run: dict, variables: list[str] | None = None, max_points: int = MAX_POINTS) -> dict:
    """{'t': [epoch s], 'series': {name: [value|None]}, 'units': {name: unit}, 'rows': n,
    'every_s': s} for `variables` (all when None). Rows are thinned evenly to
    `max_points`, always keeping the last. `every_s` is the gap between the last two
    rows: the recorder's cadence (`record every`), or None before the second row."""
    by_name = {c["name"]: c["unit"] for c in run["columns"]}
    names = [v for v in (variables if variables is not None else by_name) if v in by_name]
    key = {n: f"{n} [{by_name[n]}]" if by_name[n] else n for n in names}
    times: list[float] = []
    values: dict[str, list] = {n: [] for n in names}
    for line in _complete_lines(Path(run["_dir"]) / f"{run['id']}.jsonl"):
        row = _row(line)
        if row is None or (t := _parse_time(str(row["timestamp"]))) is None:
            continue
        times.append(t)
        for n in names:
            values[n].append(_number(row.get(key[n])))
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
