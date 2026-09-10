"""The console's single seam onto FORMS.

Every ``import forms`` in ``formslab.console`` and ``formslab.devices`` passes
through this module. Nothing else there may name the library directly.

The console is a lab tool first: it drives PSUs, the cryocooler board, RTDs and
thermocouples, none of which need an astrodynamics library. FORMS is what turns
it into a *mission* console -- the API catalog browser, the
external-resource registry, and knowing where the mission library lives. Those
are read-only reporting APIs, and none of them sit in a hot loop.

Keeping them behind one file buys two things:

* **The console runs without FORMS.** Imports here are deferred to call time, so
  a lab machine installs the transports and nothing else. A command that needs
  the library raises `FormsUnavailable`, which sessions render as a message
  instead of a traceback.
* **The dependency is countable.** The surface consumed is `SURFACES` below, and
  it is enforced by there being nowhere else to import from.

``formslab.host`` is the deliberate exception: it exists to run FORMS missions,
so it imports the library directly and requires the `[forms]` extra. The seam
protects the console, not the host.

Accessors return the *module*, not re-exported names: the console tracks the
library's own vocabulary rather than inventing a parallel one, and adding a call
to an already-bridged module needs no change here.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

# Every FORMS module the console is allowed to reach, and why. Adding a row is a
# deliberate widening of the seam — the console's dependency on FORMS is exactly
# this table.
SURFACES = {
    "forms.core.catalog": "API catalog: symbol tree for the `cmd` browser",
    "forms.bricks.resources": "External-resource registry: inventory and status",
    "forms.bricks.fetch": "External-resource registry: on-demand downloads",
    "forms.ai.manifest": "Live agent capabilities: the `skills` rank",
    "forms.core.paths": "Workspace resolution: where the mission library lives",
}


class FormsUnavailable(RuntimeError):
    """A console command needed FORMS and the library is not importable.

    Carries the offending module so a session can name the missing surface
    rather than reporting a bare import error.
    """

    def __init__(self, module: str, cause: BaseException | None = None):
        self.module = module
        reason = f" ({cause})" if cause is not None else ""
        super().__init__(
            f"{module} is unavailable{reason}. This command needs FORMS; "
            f"install it with `pip install forms` to enable it."
        )


def available() -> bool:
    """True when FORMS is importable at all.

    Cheap enough to call per keystroke: after the first call the answer is a
    dict lookup in ``sys.modules``. Sessions use it to hide or grey out
    library-backed commands rather than offering something that will fail.
    """
    try:
        import_module("forms")
    except Exception:
        return False
    return True


def _load(module: str) -> ModuleType:
    """Import one bridged module, or raise `FormsUnavailable`.

    Deferred to call time on purpose. A module-level import would make the whole
    console unimportable on a machine that has only the transports installed,
    which is the common lab case.
    """
    try:
        return import_module(module)
    except Exception as exc:                # ImportError, but also the
        raise FormsUnavailable(module, exc) from exc   # half-installed case


def catalog() -> ModuleType:
    """`forms.core.catalog` — `DEFAULT_CATALOG_PATH`, `build_catalog`,
    `find_symbols`."""
    return _load("forms.core.catalog")


def resources() -> ModuleType:
    """`forms.bricks.resources` — `resource_report`, `resource_status`,
    `resource_keys`, and the root accessors."""
    return _load("forms.bricks.resources")


def fetch() -> ModuleType:
    """`forms.bricks.fetch` — `update_resource`, `update_stale_resources`."""
    return _load("forms.bricks.fetch")


def paths():
    """`forms.core.paths` -- `missions_root`, `workspace_root`, `rscripts_dir`.

    The console does not invent its own idea of where a workspace is. FORMS
    already resolves one ($FORMS_MISSIONS_DIR, then a walk up for `missions/`,
    then a remembered choice), and `ctrl` asking the library is the only way the
    two agree about which missions exist.
    """
    return _load("forms.core.paths")


def forms_root():
    """The FORMS source tree the API catalog is built from.

    `build_catalog(project_root=...)` scans a checkout, and before the
    extraction the console simply walked up from its own file to reach it.
    From outside that checkout the only honest answer is where the library is
    actually installed: `<...>/python/src/forms/__init__.py` -> `<...>/python`.
    """
    from pathlib import Path

    return Path(_load("forms").__file__).resolve().parents[2]


def capabilities() -> list:
    """The live agent capabilities, or an empty list.

    The one accessor that swallows its failure instead of raising: the `cmd`
    browser reports the skills rank as a *count* alongside the other ranks, and
    a missing agent stack should read as "none" there, not blow up the table.
    """
    try:
        return _load("forms.ai.manifest").collect_domain_capabilities()
    except Exception:
        return []
