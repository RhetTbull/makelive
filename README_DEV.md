# Developer Notes

These are notes to help me (and other contributors) remember how to set up the project and do common tasks.

## Build Tool

This project uses [Flit](https://flit.readthedocs.io/en/latest/).  It is a simple build tool that uses `pyproject.toml` to define the project.

## Installing

- Create a virtual environment and activate it
- `pip install flit`
- `flit install`

## Linting and Formatting

Linting and formatting utilizes [ruff](https://github.com/astral-sh/ruff)

- `ruff check`
- `ruff format`

## Testing

Testing uses pytest.

Tests require that [exiftool](https://exiftool.org) is installed and available on the path.

- `mypy .`
- `python3 -m pytest`

## Building

- `rm -rf dist && rm -rf build`
- `flit build`

## Publishing

- `flit publish`
