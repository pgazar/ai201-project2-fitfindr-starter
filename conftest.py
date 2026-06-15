# Ensures the project root is importable so tests can `from tools import ...`
# regardless of pytest's import mode / where it's invoked from.
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
