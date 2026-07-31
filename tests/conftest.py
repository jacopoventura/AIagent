import os
import sys

# Ensure the project root (parent of tests/) is importable as `main`, regardless
# of how pytest is invoked or from which working directory.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# main.py raises SystemExit at import time if ANTHROPIC_API_KEY is unset. Tests
# never make real API calls (ask_claude is always mocked), so a placeholder is
# enough to satisfy the import-time check without needing a real key or .env file.
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-key-not-real")
