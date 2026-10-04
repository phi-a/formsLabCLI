"""The web GUI: a small local web app, a client of the files the console and the
sequence host already share (CAST status, the run lock, recorded CSVs, plans).

It never opens an instrument. It reads what the host publishes and, from Stage 2,
sends commands the way `labcli cast` does. Standard library only; start it with
`labcli gui`. See docs/GUI.md.
"""
