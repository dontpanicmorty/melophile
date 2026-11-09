import sys

from .cli import main as _main


def main() -> None:
    sys.exit(_main())


if __name__ == "__main__":
    main()
