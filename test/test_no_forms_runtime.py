"""formsLabCLI does not run FORMS.

FORMS is the astrodynamics engine; it computes profiles offline, and this
package runs them against the bench. Nothing here may import it -- not the
package, not the shipped rScripts. A module that starts to would quietly make
every lab machine depend on an orbit library again.
"""
import ast
from pathlib import Path

import formslab

ROOT = Path(formslab.__file__).resolve().parents[2]
SOURCES = [*Path(formslab.__file__).parent.rglob("*.py"), *(ROOT / "rScripts").glob("*.py")]


def _forms_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                 [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
        found += [n for n in names if n == "forms" or n.startswith("forms.")]
    return found


def test_nothing_imports_forms():
    offenders = {str(p.relative_to(ROOT)): imports for p in SOURCES
                 if (imports := _forms_imports(p))}
    assert offenders == {}


def test_every_shipped_rscript_loads(tmp_path):
    """Each rScript imports cleanly and defines rScript(run). (Loading
    connects to nothing: scripts open hardware on their first tick.)"""
    from formslab import rscripts

    names = sorted(p.stem for p in (ROOT / "rScripts").glob("r*.py"))
    run = rscripts.Run(record_dir=tmp_path)
    assert rscripts.load(run, names) == names
