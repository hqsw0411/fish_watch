"""Package application source and optional built UI; data stays external."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

from tools.paths import ROOT
DIRECTORIES = {"backend", "frontend", "config", "tools", "models", "deploy",
               "requirements", "docs", "tests", "data-manifests"}
TOP_FILES = {"README.md", ".gitignore", ".gitattributes", ".env.example", "pyproject.toml"}
SUFFIXES = {".py", ".sh", ".sbatch", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
            ".json", ".geojson", ".md", ".txt", ".toml", ".yaml", ".yml", ".html",
            ".css", ".scss", ".svg", ".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".ttf"}
SKIP = {"data", "runs", "model-artifacts", "output", "outputs", "backups", "node_modules",
        "__pycache__", ".git", ".venv", ".pytest_cache", ".conda-acs"}
REQUIRED = {"backend/app/main.py", "backend/app/prediction.py", "frontend/src/main.tsx",
            "frontend/public/land.geojson", "frontend/package.json", "frontend/package-lock.json",
            "models/features.py", "models/random_forest.py", "docs/data_dictionary.json",
            "tools/data/build_regional_dataset.py", "tools/release/package_code.py"}


def collect_files(root: Path, include_build: bool = False) -> list[Path]:
    root = root.resolve()
    selected = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] not in DIRECTORIES and rel.as_posix() not in TOP_FILES:
            continue
        if any(part in SKIP - {"data"} for part in rel.parts):
            continue
        if "data" in rel.parts and rel.parts[:2] != ("tools", "data"):
            continue
        if "dist" in rel.parts and not (include_build and rel.parts[:2] == ("frontend", "dist")):
            continue
        if path.is_symlink():
            raise ValueError(f"Source symlink is not portable: {rel}")
        if not path.is_file():
            continue
        if path.name.startswith(".env") and rel.as_posix() != ".env.example":
            continue
        if path.suffix.lower() not in SUFFIXES and rel.as_posix() not in TOP_FILES:
            continue
        if not path.resolve().is_relative_to(root) or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError(f"Unsafe or unexpectedly large source: {rel}")
        selected.append(path)
    present = {p.relative_to(root).as_posix() for p in selected}
    required = REQUIRED | ({"frontend/dist/index.html"} if include_build else set())
    if required - present:
        raise FileNotFoundError("Missing application source/assets: " + ", ".join(sorted(required - present)))
    return sorted(selected)


def package(root: Path, target: Path, include_build: bool = False) -> dict:
    root = root.resolve()
    files = collect_files(root, include_build)
    contents, entries, total = [], [], 0
    for path in files:
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("Source changed during packaging")
        total += len(raw)
        if total > 64 * 1024 * 1024:
            raise ValueError("Code package unexpectedly exceeds 64 MiB")
        name = path.relative_to(root).as_posix()
        contents.append((name, raw))
        entries.append({"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"package_root": "fisheries-server", "data_included": False,
                "built_frontend_included": include_build, "files": entries}
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as output, tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, raw in contents + [("deployment-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())]:
            info = tarfile.TarInfo("fisheries-server/" + name)
            info.size = len(raw)
            info.mode = 0o755 if name.endswith((".sh", ".sbatch")) else 0o644
            bundle.addfile(info, io.BytesIO(raw))
    return {"archive": str(target), "files": len(entries), "data_included": False,
            "bytes": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/fish-watch-code.tar.gz")
    parser.add_argument("--include-built-frontend", action="store_true")
    args = parser.parse_args()
    print(json.dumps(package(ROOT, args.output.resolve(), args.include_built_frontend), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
