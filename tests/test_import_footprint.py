# tests/test_import_footprint.py
"""The dashboard pages must not load the assistant's heavy dependencies.

Home, Threshold Strategy, and Peak Detection import only ``utils``. Keeping
the LLM SDKs, MCP, the embedding model, and FAISS out of that import graph
keeps those pages fast to start and working even if one of those packages
fails to install or import on Streamlit Community Cloud.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
HEAVY = ["anthropic", "openai", "mcp", "fastembed", "faiss", "requests", "bs4",
         "pypdf", "yaml", "assistant", "tools", "rag"]


def test_dashboard_imports_skip_heavy_dependencies():
    code = (
        "import sys, utils, utils.analysis, utils.peak_detection\n"
        f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    loaded = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert loaded == ""
