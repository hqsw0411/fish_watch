"""Compatibility entrypoint for code-only packaging."""
from package_code import collect_files, main

if __name__ == "__main__":
    raise SystemExit(main())
