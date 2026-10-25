"""Project paths and JSON conversion shared by command-line modules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Cannot encode {type(value).__name__}")
