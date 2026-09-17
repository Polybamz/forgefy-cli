"""PyInstaller entry point.

pointing PyInstaller directly at src/forgefy_cli/cli.py breaks its relative
imports (it gets executed as a top-level script, not as part of the
forgefy_cli package). This tiny wrapper imports the package properly so
PyInstaller's analysis walks it as `forgefy_cli.cli`, matching the
`forgefy = "forgefy_cli.cli:main"` console-script entry point in
pyproject.toml.
"""
from forgefy_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
