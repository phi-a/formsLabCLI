"""The sequence host: running rScripts against lab hardware, with or without
a FORMS mission around them.

Lab modes (`modes.LAB_MODES`) need only formsLabCLI. Every other mode needs
the optional `[forms]` extra; those imports are made when the mode starts, so
this package imports on a lab machine without FORMS, and the console's `ctrl`
tab refuses a FORMS mode up front rather than failing mid-launch.
"""
