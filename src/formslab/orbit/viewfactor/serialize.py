"""Persistence helpers for Loading collections."""

import json
from pathlib import Path

import numpy as np

from .propagator import Loading

_ARRAY_FIELDS = (
    'u', 'earth', 'albedo', 'solar',
    'panel', 'structure', 'space', 'eclipse',
)


def _meta_path(npz_path):
    p = Path(npz_path)
    return p.parent / (p.stem + '_meta.json')


def save_profiles(profiles, npz_path, meta_path=None, **extra):
    """Persist Loading list to .npz + companion JSON."""
    npz_path  = Path(npz_path)
    meta_path = Path(meta_path) if meta_path else _meta_path(npz_path)

    arrays = {}
    meta   = {'facets': [], **extra}
    for idx, p in enumerate(profiles):
        key = f's{idx}'
        for field in _ARRAY_FIELDS:
            arrays[f'{key}_{field}'] = getattr(p, field)
        meta['facets'].append({
            'index':  idx,
            'name':   p.name,
            'width':  float(p.width),
            'height': float(p.height),
        })

    np.savez(npz_path, **arrays)
    meta_path.write_text(json.dumps(meta, indent=2))


def load_profiles(npz_path, meta_path=None):
    """Load Loading list from .npz + companion JSON."""
    npz_path  = Path(npz_path)
    meta_path = Path(meta_path) if meta_path else _meta_path(npz_path)

    data = np.load(npz_path)
    meta = json.loads(meta_path.read_text())

    profiles = []
    for s in meta['facets']:
        key = f"s{s['index']}"
        profiles.append(Loading(
            name=s['name'],
            u=data[f'{key}_u'],
            width=s['width'],
            height=s['height'],
            earth=data[f'{key}_earth'],
            albedo=data[f'{key}_albedo'],
            solar=data[f'{key}_solar'],
            panel=data[f'{key}_panel'],
            structure=data[f'{key}_structure'],
            space=data[f'{key}_space'],
            eclipse=data[f'{key}_eclipse'],
        ))
    return profiles, meta
