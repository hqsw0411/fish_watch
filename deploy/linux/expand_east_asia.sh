#!/usr/bin/env bash
# Run from an interactive server terminal; credentials stay in the official tool.
set -euo pipefail
fisheries_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$fisheries_root"
fisheries_python="${FISHERIES_PYTHON:-$fisheries_root/.venv/bin/python}"
if [[ ! -x "$fisheries_python" ]]; then
  echo "Create .venv with Python 3.12+ and install backend/requirements.txt first." >&2
  exit 1
fi
if [[ "${1:-}" == "--login" ]]; then
  "$fisheries_python" -c 'import copernicusmarine; raise SystemExit(0 if copernicusmarine.login() else 1)'
  shift
fi
"$fisheries_python" scripts/build_regional_dataset.py \
  --config config/east_asia_2012_2024.json --mode toolbox "$@"
if [[ " $* " != *" --plan "* ]]; then
  echo "Dataset built. To serve it, set these variables in your service configuration and restart:"
  echo 'export FISHERIES_CONFIG="$PWD/config/east_asia_2012_2024.json"'
  echo 'export FISHERIES_SAMPLE_DIR="$PWD/data/samples/east_asia_2012_2024"'
  echo 'bash deploy/linux/start.sh'
fi
