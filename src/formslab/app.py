"""The `labcli` entry point.

`labcli <command>` runs one command and exits (formslab.cli): for SSH and
scripts. A bare `labcli`, `labcli watch`, `--demo` or `--no-monitor` opens the
watch screen (formslab.console.watch). `formslab` is an installed package, so
imports resolve on their own and both run from any working directory.
"""
import sys

SCREEN = ("watch", "--demo", "--no-monitor")


def main():
    argv = sys.argv[1:]
    if argv and argv[0] not in SCREEN:
        from formslab.cli import main as one_command
        sys.exit(one_command(argv))
    from formslab.console.watch import main as watch
    sys.exit(watch(argv))


if __name__ == "__main__":
    main()
