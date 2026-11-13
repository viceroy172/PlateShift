import sys

COMMANDS = {"convert", "inspect", "printers", "filaments", "-h", "--help"}


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in COMMANDS:
        from .cli import main as cli_main
        return cli_main(args)
    # no command: open the window (any .3mf args get converted straight away)
    from .gui import main as gui_main
    return gui_main(args)


if __name__ == "__main__":
    sys.exit(main())
