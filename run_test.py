#!/usr/bin/env python
"""Run this server's test suite.

Standalone repo, so there is no extra test gate.
Use the venv that has pytest + fastmcp installed:

    .venv/bin/python run_tests.py [pytest args...]
"""

import sys

import pytest

if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv[1:] or ["tests/", "-v"]))
