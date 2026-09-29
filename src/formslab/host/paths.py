"""Where the host's IPC files live (lock, session, events, stream snapshot).

With FORMS installed this is FORMS' own ``run_dir()``, so an attached Zenith
frontend and the host agree. Without it, ``<output>/.run``.
"""
from pathlib import Path

from formslab.config import output_dir


def run_dir() -> Path:
    try:
        from forms.core.paths import run_dir as forms_run_dir
    except ImportError:
        path = output_dir() / ".run"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return forms_run_dir()
