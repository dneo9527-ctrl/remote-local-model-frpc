"""Use the unified source packager from the repository root."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parents[3] / "tools/package_source.py"), run_name="__main__")
