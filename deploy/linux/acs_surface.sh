#!/usr/bin/env bash
# ACS helper for the monthly, surface-only East Asia dataset.
# Never changes an already running service or rewrites the research SQLite DB.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
python="${FISHERIES_PYTHON:-$root/.venv/bin/python}"
mode="${1:-help}"
case "$mode" in
  help)
    echo 'Usage: bash deploy/linux/acs_surface.sh {preflight|login|plan|build|verify}'
    echo 'Run build in a platform-approved compute job/interactive allocation, not a login node.'
    ;;
  preflight)
    echo 'Project:' "$root"
    echo 'Disk:'
    df -h .
    echo 'Quota (if supported):'
    quota -s 2>&1 || true
    echo 'Python:'
    python3 --version
    echo 'Available virtual environment:'
    if [[ -x "$python" ]]; then "$python" --version; else echo 'Create .venv before running plan/build/login'; fi
    echo 'Remote access checks (headers only, not a guarantee of download access):'
    if command -v curl >/dev/null 2>&1; then
      curl -ILfsS --max-time 15 'https://zenodo.org/records/14982712' >/dev/null || echo 'Zenodo check failed'
      curl -ILfsS --max-time 15 'https://data.marine.copernicus.eu/products' >/dev/null || echo 'Copernicus check failed'
    fi
    ;;
  login)
    [[ -x "$python" ]] || { echo 'Missing .venv; install requirements/data.txt first' >&2; exit 2; }
    "$python" -m copernicusmarine login
    ;;
  plan)
    [[ -x "$python" ]] || { echo 'Missing .venv; install requirements/data.txt first' >&2; exit 2; }
    "$python" -m tools.data.build_regional_dataset --config config/east_asia_2012_2024.json --plan
    ;;
  build)
    [[ -x "$python" ]] || { echo 'Missing .venv; install requirements/data.txt first' >&2; exit 2; }
    mkdir -p output
    echo 'Starting 13-year surface-only download and build. This may take a long time.'
    echo 'Existing fisheries-vue remains unchanged; release stays in data/samples/east_asia_2012_2024.'
    "$python" -m tools.data.build_regional_dataset \
       --config config/east_asia_2012_2024.json --mode toolbox 2>&1 | tee -a output/acs_surface_2012_2024.log
    "$python" -m tools.data.validate_surface_release
    ;;
  verify)
    [[ -x "$python" ]] || { echo 'Missing .venv; install requirements/data.txt first' >&2; exit 2; }
    "$python" -m tools.data.validate_surface_release
    ;;
  *)
    echo 'Unknown mode. Run: bash deploy/linux/acs_surface.sh help' >&2; exit 2
    ;;
esac
