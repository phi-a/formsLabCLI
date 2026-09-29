from formslab.console.sessions.base import ConsoleSession
from formslab.console.cast import castcli

class CastSession(ConsoleSession):
    # status_panel() reads castfile.json -- no device I/O.
    live_status = True

    def __init__(self):
        super().__init__("cast", castcli.execute_command)

    def help(self):
        return castcli.help_panel()

    def banner(self):
        return castcli.status_panel()
