"""Launcher script — run this file from anywhere."""

import sys
import os

# Ensure the project root is on the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from coding_agent.__main__ import main

if __name__ == "__main__":
    main()
