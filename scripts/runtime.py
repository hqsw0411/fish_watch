"""Use workspace packages when present, otherwise the active Python environment."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / ".python-deps", ROOT / ".week1-deps"):
    if directory.is_dir():
        sys.path.insert(0, str(directory))


def json_default(value):
    """Preserve numerical NetCDF attributes as JSON numbers/lists."""
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot encode {type(value).__name__}")
