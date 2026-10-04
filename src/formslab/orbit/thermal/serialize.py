"""Persistence helpers for panel temperature caches."""

import json
from pathlib import Path

import numpy as np


def _meta_path(npz_path):
    p = Path(npz_path)
    return p.parent / (p.stem + '_meta.json')


def save_temperatures(panel_data, npz_path, meta_path=None):
    """Save panel temperature traces to .npz + companion JSON.

    Parameters
    ----------
    panel_data : dict[str, dict]
        ``{panel_name: {'hot': ndarray [n, ny, nx], 'cold': ndarray [n, ny, nx]}}``
    npz_path   : path-like
    meta_path  : path-like, optional
    """
    npz_path  = Path(npz_path)
    meta_path = Path(meta_path) if meta_path else _meta_path(npz_path)

    arrays = {}
    meta   = {'panels': []}
    for idx, (name, d) in enumerate(panel_data.items()):
        key = f'p{idx}'
        arrays[f'{key}_hot']  = d['hot']
        arrays[f'{key}_cold'] = d['cold']
        meta['panels'].append({'index': idx, 'name': name})

    np.savez(npz_path, **arrays)
    meta_path.write_text(json.dumps(meta, indent=2))


def load_temperatures(npz_path, meta_path=None):
    """Load panel temperature traces.

    Returns
    -------
    dict[str, dict] — ``{name: {'hot': ndarray, 'cold': ndarray}}``
    """
    npz_path  = Path(npz_path)
    meta_path = Path(meta_path) if meta_path else _meta_path(npz_path)

    data = np.load(npz_path)
    meta = json.loads(meta_path.read_text())

    result = {}
    for p in meta['panels']:
        key = f"p{p['index']}"
        result[p['name']] = {
            'hot':  data[f'{key}_hot'],
            'cold': data[f'{key}_cold'],
        }
    return result
