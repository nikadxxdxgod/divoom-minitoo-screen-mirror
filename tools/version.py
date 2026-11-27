"""Print the program version (used by build.bat)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import minitoo  # noqa: E402

print(minitoo.__version__)
