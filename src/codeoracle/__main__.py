"""Package entrypoint for `python -m codeoracle`."""

from codeoracle.cli.__main__ import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
