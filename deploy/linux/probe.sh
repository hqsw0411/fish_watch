#!/usr/bin/env bash
# Read-only ACS server inventory for the fisheries data deployment.
# Run from WebShell:
#   bash deploy/linux/probe.sh
set +e

STAMP="$(date -u +%Y%m%dT%H%M%SZ 2>/dev/null || date)"
REPORT_DIR="${FISHERIES_REPORT_DIR:-output/diagnostics}"
mkdir -p "$REPORT_DIR" || exit 1
REPORT="$REPORT_DIR/fisheries_server_probe_${STAMP}.txt"
exec > >(tee "${REPORT}") 2>&1

echo "=== fisheries server probe ==="
echo "timestamp_utc=${STAMP}"
echo
echo "=== host ==="
hostname 2>/dev/null
uname -a 2>/dev/null
printf 'user='; id -un 2>/dev/null
printf 'uid='; id 2>/dev/null
echo
echo "=== compute and memory ==="
printf 'nproc='; nproc 2>/dev/null
free -h 2>/dev/null
echo
echo "=== storage ==="
df -h 2>/dev/null
echo
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT 2>/dev/null
echo
echo "=== runtimes ==="
for command_name in python3 python conda docker psql duckdb srun sbatch git curl; do
  printf '%-8s ' "${command_name}"
  command -v "${command_name}" 2>/dev/null || echo "missing"
done
python3 --version 2>/dev/null
python --version 2>/dev/null
echo
echo "=== python packages ==="
python3 - <<'PY'
try:
    import importlib.util
except Exception:
    raise SystemExit
for name in ("pandas", "numpy", "xarray", "zarr", "dask", "pyarrow", "duckdb", "sqlalchemy", "psycopg"):
    print(f"{name}={'installed' if importlib.util.find_spec(name) else 'missing'}")
PY
echo
echo "=== scheduler environment ==="
env | grep -E '^(SLURM|PBS|LSB|CUDA|CONDA)' | sed 's/=.*$/=<set>/' 2>/dev/null
echo
echo "=== outbound endpoint checks ==="
for url in "https://zenodo.org/api/records/14982712" "https://data.marine.copernicus.eu/products"; do
  printf '%s: ' "${url}"
  curl -L -I --max-time 15 --silent --show-error "${url}" 2>&1 | head -n 1
done
echo
echo "report=${REPORT}"
