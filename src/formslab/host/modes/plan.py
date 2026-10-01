"""Lab plan mode: a `.forms` plan's rScripts on a `LabForms` handle. No FORMS.

The plan names its rScripts and its CSV cadence; the host then runs the plan's
Sequence (`formslab.sequence.LabSequenceRunner`). A plan whose rScripts do not
all load does not start: its commands would only time out against a missing
instrument owner.
"""
from formslab import rscripts
from formslab.sequence import PlanError, load_plan


def initialize(path):
    plan = load_plan(path)
    forms = rscripts.LabForms(name=plan.name)
    loaded = rscripts.load(forms, plan.rscripts)
    missing = [n for n in plan.rscripts if n not in loaded]
    if missing:
        raise PlanError(f"{plan.name}: rScripts did not load: {', '.join(missing)} "
                        "(the log above says why)")
    forms.record(value=plan.record_interval, unit=plan.record_unit)
    return forms, plan
