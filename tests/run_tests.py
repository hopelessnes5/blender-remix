"""Run the test suite inside Blender.

The add-on imports ``bpy`` from its ``__init__``, so the tests cannot run in a
plain Python interpreter.  Use Blender's background mode::

    blender.exe --background --factory-startup --python tests/run_tests.py

Exit code is non-zero when a test fails, so the command can gate CI.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

SUITE_DIRS = ("tests/unit", "tests/blender")


def build_suite() -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for relative in SUITE_DIRS:
        directory = os.path.join(REPO_ROOT, relative)
        suite.addTests(loader.discover(directory, pattern="test_*.py", top_level_dir=directory))
    return suite


def main() -> int:
    print(f"Running RTX Remix direct-backend tests from {REPO_ROOT}")
    result = unittest.TextTestRunner(verbosity=2).run(build_suite())
    total = result.testsRun
    failures = len(result.failures) + len(result.errors)
    print(f"\nRan {total} tests: {total - failures} passed, {len(result.failures)} failed, {len(result.errors)} errors")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
