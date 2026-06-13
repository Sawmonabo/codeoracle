"""`python -m codeoracle` entry point.

A Typer root app declared as a command group (the no-op `@app.callback()`
keeps it a group even when only one command is registered — see Plan 5
Task 14 for the first one, `codeoracle index`). Keeping the entry point here in
Plan 1 means every later plan can reference `python -m codeoracle <subcommand>`
without coupling its task list to Plan 5's CLI work.
"""

import typer

app = typer.Typer()


@app.callback()
def main() -> None:
    """CodeOracle command-line interface."""


if __name__ == "__main__":
    app()
