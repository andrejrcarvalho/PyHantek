"""Entry point for the frozen (PyInstaller) build."""

import sys

from pyhantek.app import main

if __name__ == "__main__":
    sys.exit(main())
