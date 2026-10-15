"""The `turno` command (`turno.cli:main`); every subcommand is defined in `autopilot.main`."""


def main():
    from . import autopilot

    autopilot.main()
