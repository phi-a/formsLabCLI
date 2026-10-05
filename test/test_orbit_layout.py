"""formslab.orbit is a layer stack.

Each layer imports only the layers below it, by relative imports. The models
(every layer) depend on no other part of formsLabCLI, so they can be read and
tested on their own; only the orbit file (`file.py`, on top) reaches into the
rest, for the grammar its lines are read with. These checks parse the source.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import formslab

PACKAGE = Path(formslab.__file__).parent / "orbit"

# Lowest layer first; a layer may import only from the layers listed for it.
LAYERS = ["propagate", "geometry", "viewfactor", "thermal", "visibility", "imaging"]
ALLOWED = {
    "propagate": set(),
    "geometry": {"propagate"},
    "viewfactor": {"propagate", "geometry"},
    "thermal": {"propagate", "geometry", "viewfactor"},
    "visibility": {"propagate"},
    "imaging": {"propagate", "visibility"},
}

# Third-party imports: numpy (base) and the `orbit` extra; anything else must be stdlib.
EXTRA = {"numpy", "scipy", "matplotlib", "mpl_toolkits"}
# The orbit file may import these: its grammar, and where plans (and orbits) are found.
FILE_USES = {"formslab.rscripts.grammar", "formslab.sequence.plan"}
# Optional, outside the extra: only these modules may import them, and no
# package __init__ may import these modules.
OPTIONAL = {"plotly": "geometry/cubesat/scene3d.py"}


def _imports(path: Path) -> list[str]:
    """Imported module names, relative ones with their leading dots."""
    mods = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append("." * node.level + (node.module or ""))
    return mods


class OrbitLayoutTests(unittest.TestCase):
    def test_the_models_import_no_other_part_of_formslab(self) -> None:
        """The layers: only themselves (relatively), stdlib, numpy and the extra.
        The orbit file: those, and the grammar."""
        import sys

        offenders = []
        for path in PACKAGE.rglob("*.py"):
            for mod in _imports(path):
                if mod.startswith("."):
                    continue
                if path == PACKAGE / "file.py" and mod in FILE_USES:
                    continue
                top = mod.split(".")[0]
                if top in EXTRA or top in sys.stdlib_module_names or top == "__future__":
                    continue
                if OPTIONAL.get(top) == path.relative_to(PACKAGE).as_posix():
                    continue
                offenders.append(f"{path.relative_to(PACKAGE)}: {mod}")
        self.assertEqual(offenders, [])

    def test_layers_import_only_lower_layers(self) -> None:
        offenders = []
        for layer in LAYERS:
            for path in (PACKAGE / layer).rglob("*.py"):
                # From orbit/<layer>/f.py a sibling layer is `..x`; one level
                # deeper it is `...x`.
                depth = len(path.relative_to(PACKAGE / layer).parts)
                for mod in _imports(path):
                    dots = len(mod) - len(mod.lstrip("."))
                    target = mod.lstrip(".").split(".")[0]
                    if dots != depth + 1:
                        continue
                    if target in LAYERS and target not in ALLOWED[layer]:
                        offenders.append(f"{path.relative_to(PACKAGE)}: {mod}")
        self.assertEqual(offenders, [], f"layering violations: {offenders}")

    def test_optional_modules_are_not_imported_by_packages(self) -> None:
        """plotly-only views stay opt-in: no __init__ pulls them in."""
        stems = {Path(m).stem for m in OPTIONAL.values()}
        offenders = [f"{p.relative_to(PACKAGE)}: {mod}"
                     for p in PACKAGE.rglob("__init__.py")
                     for mod in _imports(p) if mod.lstrip(".").split(".")[-1] in stems]
        self.assertEqual(offenders, [])

    def test_bare_package_import_is_free(self) -> None:
        """`import formslab.orbit` pulls in nothing, so a base install can probe it."""
        import formslab.orbit  # noqa: F401


class OrbitEntryPointTests(unittest.TestCase):
    def test_public_entry_points(self) -> None:
        from formslab.orbit.imaging import Instrument, build_pass_case, schedule_date
        from formslab.orbit.propagate import Orbit
        from formslab.orbit.thermal import evaluate_pass
        from formslab.orbit.thermal.pipeline import CubeSat, catalog, env, flux, transient, view
        from formslab.orbit.visibility import Target, observation_window

        for obj in (Orbit, CubeSat, catalog, view, flux, transient, env, evaluate_pass,
                    observation_window, schedule_date, build_pass_case, Instrument, Target):
            self.assertTrue(callable(obj))

    def test_routines_use_shared_visibility_primitives(self) -> None:
        from formslab.orbit.imaging.pass_case import build_pass_case
        from formslab.orbit.visibility import Target

        self.assertIs(build_pass_case.__globals__["Target"], Target)


if __name__ == "__main__":
    unittest.main()
