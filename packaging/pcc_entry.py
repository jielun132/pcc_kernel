"""Absolute imports are required by the frozen executable entry point."""
from pcc_tool.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
