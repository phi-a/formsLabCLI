"""Every module in the package imports, on a base install.

The extraction moved ~50 modules out of a tree that only worked because
`setenv.py` put five directories on `sys.path` and chdir'd into the FORMS
checkout. Nothing there was import-checked as a package, so this walks the whole
package and imports each module. It is the test that would have caught every
mistake in the move.

"Base install" means `pip install -e .` with no extras: no FORMS, no mpremote,
no Qt. A module that needs one of those must defer the import to call time.
"""

import importlib
import os
import pkgutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import formslab

# Firmware, not a module. `devices/pico_board_control.py` is deployed onto the
# Raspberry Pi Pico and imports MicroPython's `machine`, which does not exist on
# the PC. It ships as package data; it is never imported here.
FIRMWARE = {"formslab.devices.pico_board_control"}

# Modules that legitimately need an extra, and the distribution that provides
# it. The Qt analysis tab imports at module scope on purpose: it is simply
# unavailable without its extra, and the console already renders an unbuildable
# tab as unavailable. The map is asserted in both directions below, so a module
# that becomes lazy has to be removed from here.
#
# The two plotting scripts that used to be listed here are not modules at all
# and now live in `scripts/` -- see `scripts/README.md`.
EXTRA_ONLY = {
    "formslab.console.analysis.analysiscli": "matplotlib",
    "formslab.console.sessions.analysis": "matplotlib",
    # The sequence host: exists to run FORMS missions, so it needs `[forms]`.
    "formslab.host.sequence": "forms",
    "formslab.host.stream": "forms",
    "formslab.host.modes.tvac": "forms",
    "formslab.host.modes.darkness": "forms",
    "formslab.host.modes.axionsat": "forms",
}


def _all_modules():
    for info in pkgutil.walk_packages(formslab.__path__, prefix="formslab."):
        if info.name in FIRMWARE:
            continue
        yield info.name


class PackageImports(unittest.TestCase):

    def test_every_module_imports(self):
        found = sorted(_all_modules())
        self.assertGreater(len(found), 40,
                           "package walk found suspiciously few modules")
        for name in found:
            if name in EXTRA_ONLY:
                continue
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_extra_only_modules_are_still_extra_only(self):
        """Keeps EXTRA_ONLY honest in both directions.

        Every module listed must actually fail for the named distribution -- if
        one is made lazy, or its extra gets installed into the base set, this
        says so instead of quietly exempting a module that no longer needs it.
        """
        for name, dist in EXTRA_ONLY.items():
            with self.subTest(module=name):
                try:
                    importlib.import_module(dist)
                except ModuleNotFoundError:
                    pass
                else:
                    self.skipTest(f"{dist} is installed; extras are in play")
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(name)

    def test_firmware_is_present_but_not_imported(self):
        """The Pico firmware must ship, and must not be importable on the PC."""
        firmware = (Path(formslab.__file__).parent / "devices"
                    / "pico_board_control.py")
        self.assertTrue(firmware.exists(), f"missing firmware: {firmware}")

    def test_entry_point_is_callable(self):
        from formslab.app import main
        self.assertTrue(callable(main))

    def test_console_tabs_construct(self):
        """Each tab the REPL offers can actually be built on a base install."""
        from formslab.app import TAB_FACTORIES

        self.assertNotIn("book", TAB_FACTORIES)

        for tab, factory in TAB_FACTORIES.items():
            with self.subTest(tab=tab):
                factory()


class ImportIsFree(unittest.TestCase):
    """Importing a module must not touch the filesystem.

    `psucli` used to build its PSU objects at module scope, which read the
    hardware map, which seeded the config directory -- so merely
    `import formslab.console.psu.psucli` dropped a file in the operator's home.
    Anything that reads configuration has to do it when asked, not when loaded.

    Runs in a subprocess: the modules under test are already imported in this
    one, so an in-process check would prove nothing.
    """

    def test_importing_every_module_creates_no_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A path that does not exist yet, inside a directory that does.
            unused = Path(tmp) / "should-not-be-created"
            env = dict(os.environ,
                       FORMSLAB_CONFIG_DIR=str(unused),
                       FORMSLAB_OUTPUT_DIR=str(Path(tmp) / "out-unused"))

            proc = subprocess.run(
                [sys.executable, "-c", _IMPORT_EVERYTHING],
                env=env, capture_output=True, text=True, timeout=120)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(
                unused.exists(),
                "importing the package created the config directory; some "
                "module reads configuration at import time")


_IMPORT_EVERYTHING = """
import importlib, pkgutil, formslab
for info in pkgutil.walk_packages(formslab.__path__, "formslab."):
    if info.name.endswith("pico_board_control"):
        continue
    try:
        importlib.import_module(info.name)
    except ModuleNotFoundError:
        pass
"""


if __name__ == "__main__":
    unittest.main()
