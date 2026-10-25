#!/usr/bin/env bash
# Start the prebuilt web UI and read-only API from the project root.
set -euo pipefail
fisheries_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$fisheries_root"
fisheries_python="${FISHERIES_PYTHON:-$fisheries_root/.venv/bin/python}"
if [[ ! -x "$fisheries_python" ]]; then
  echo "Python environment missing. Create .venv and install requirements/runtime.txt." >&2
  exit 1
fi
"$fisheries_python" -m tools.release.check_deployment
# One worker bounds memory and pins a single published release.
exec "$fisheries_python" -m uvicorn backend.app.main:app \
  --host "${FISHERIES_HOST:-127.0.0.1}" \
  --port "${FISHERIES_PORT:-8000}" \
  --workers 1
