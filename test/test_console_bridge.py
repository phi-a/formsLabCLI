"""The console's FORMS seam: one file, and the console runs without it.

`formslab/bridge.py` is the only place in this package allowed to import
`forms`. Two properties matter and neither is visible from reading a single
module, so they are asserted here:

* **Countability** — no module in `formslab` imports `forms` except the bridge.
  This keeps the console's dependency on the library a list you can read rather
  than a search you have to run. It is what made the extraction from the FORMS
  repository tractable, and it is what keeps `forms` an optional extra now
  rather than a hidden requirement.
* **Degradation** — with `forms` unimportable, the console still imports and a
  library-backed command reports a readable message instead of raising through
  the REPL. A lab machine drives a PSU without an astrodynamics library
  installed.

The second is checked against a meta-path finder that blocks `forms`, which is
the only honest way to test it from inside a checkout that has FORMS installed.
"""

import importlib
import sys
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "formslab"

# The rule covers the console and the drivers: `devices/` never had a reason to
# name FORMS and must not grow one.
#
# `host/` is exempt, and it is the only exemption. It is not the console
# optionally reaching FORMS -- it exists to run FORMS missions, needs the
# `[forms]` extra, and does not import at all without it. Routing its fifteen
# library symbols through the bridge would make the seam a directory of
# pass-throughs and hide the very distinction it is drawn to show.
BRIDGE = "bridge.py"
EXEMPT_TREE = "host/"


def _package_sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel == BRIDGE or rel.startswith(EXEMPT_TREE):
            continue
        yield rel, path.read_text(encoding="utf-8")


class _BlockForms:
    """Meta-path finder that makes `forms` and its submodules unimportable."""

    def find_spec(self, name, path=None, target=None):
        if name == "forms" or name.startswith("forms."):
            raise ImportError(f"blocked: {name}")
        return None


class ConsoleImportsForOnlyViaBridge(unittest.TestCase):

    def test_no_package_module_imports_forms_directly(self):
        offenders = []
        for rel, text in _package_sources():
            for n, line in enumerate(text.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("from forms.", "from forms ",
                                        "import forms.", "import forms ")) \
                        or stripped in ("import forms",):
                    offenders.append(f"{rel}:{n}: {stripped}")
        self.assertEqual(
            offenders, [],
            "formslab must reach FORMS only through formslab/bridge.py; route "
            "these through a bridge accessor:\n  " + "\n  ".join(offenders))

    def test_every_bridged_surface_resolves(self):
        """Each module the bridge claims to reach actually imports.

        Catches the bridge going stale against a library rename — the failure
        mode a single seam otherwise hides until a lab operator hits it. Needs
        the optional `[forms]` extra; on a bare lab install there is nothing to
        check against, and the degradation tests below cover that case instead.
        """
        import formslab.bridge as bridge

        if not bridge.available():
            self.skipTest("needs the optional [forms] extra")

        for module in bridge.SURFACES:
            with self.subTest(module=module):
                importlib.import_module(module)


class ConsoleRunsWithoutForms(unittest.TestCase):
    """Every assertion here runs with `forms` blocked from importing."""

    def setUp(self):
        self._blocker = _BlockForms()
        self._saved = {name: mod for name, mod in sys.modules.items()
                       if name == "forms" or name.startswith("forms.")
                       or name.startswith("formslab.")}
        for name in self._saved:
            del sys.modules[name]
        sys.meta_path.insert(0, self._blocker)

    def tearDown(self):
        sys.meta_path.remove(self._blocker)
        for name in [n for n in sys.modules
                     if n.startswith("formslab.") or n == "forms"
                     or n.startswith("forms.")]:
            del sys.modules[name]
        sys.modules.update(self._saved)

    def test_console_modules_import(self):
        for name in ("formslab.bridge",
                     "formslab.console.cmd_browser",
                     "formslab.console.resources"):
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_available_reports_false_rather_than_raising(self):
        bridge = importlib.import_module("formslab.bridge")
        self.assertFalse(bridge.available())

    def test_accessor_raises_typed_error_naming_the_module(self):
        bridge = importlib.import_module("formslab.bridge")
        with self.assertRaises(bridge.FormsUnavailable) as caught:
            bridge.resources()
        self.assertEqual(caught.exception.module, "forms.bricks.resources")
        self.assertIn("pip install forms", str(caught.exception))

    def test_capabilities_reports_none_instead_of_raising(self):
        """The `cmd` browser counts the skills rank; absent means zero."""
        bridge = importlib.import_module("formslab.bridge")
        self.assertEqual(bridge.capabilities(), [])

    def test_a_library_backed_command_renders_as_an_error_line(self):
        """What the operator actually sees: a message, not a traceback."""
        resources = importlib.import_module("formslab.console.resources")
        base = importlib.import_module("formslab.console.sessions.base")

        class Probe(base.ConsoleSession):
            def __init__(self):
                super().__init__(
                    "res",
                    lambda parts: resources.handle_resources_command(
                        " ".join(parts)))

            def help(self):
                return base.CLIResult(content="")

        rendered = str(Probe().handle("resources").content)
        self.assertIn("pip install forms", rendered)


if __name__ == "__main__":
    unittest.main()
