"""Host modes: what a `run <mode>` loads.

Lab modes run on a `formslab.rscripts.LabForms` handle and never import FORMS;
the rest build a FORMS instance. Both the console (`ctrl`) and the host read
this tuple, so a mode cannot be launchable in one and FORMS-only in the other.
"""

LAB_MODES = ("laco",)
