"""Resolve external sample data once per worker, independently of cwd."""
from __future__ import annotations

import json
import os
from pathlib import Path


def anchored_path(value: str, root: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def resolve_sample_dir(root: Path) -> Path:
    config_path = anchored_path(os.getenv("FISHERIES_CONFIG", "config/sample_region.json"), root)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    directory = anchored_path(os.getenv("FISHERIES_SAMPLE_DIR", config["output_dir"]), root)
    if (directory / "manifest.json").is_file():
        return directory
    pointer_path = directory / "latest.json"
    if pointer_path.is_file():
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        relative = Path(pointer["directory"])
        if relative.is_absolute():
            raise ValueError("Release pointer must be relative")
        release = (directory / relative).resolve()
        if release == directory or not release.is_relative_to(directory):
            raise ValueError("Release pointer escapes publication directory")
        if not (release / "manifest.json").is_file():
            raise FileNotFoundError("Release manifest is missing")
        return release
    return directory
