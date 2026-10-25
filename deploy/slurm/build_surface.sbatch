#!/usr/bin/env bash
#SBATCH --job-name=fish-watch-data
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=12:00:00
#SBATCH --output=slurm-%x-%j.log

# Submit from an isolated project/data workspace after checking cluster quotas.
# This file does not register a job merely by being present in the repository.
set -euo pipefail
fisheries_root="${SLURM_SUBMIT_DIR:-$PWD}"
cd "$fisheries_root"
fisheries_python="${FISHERIES_PYTHON:-$fisheries_root/.venv/bin/python}"
[[ -x "$fisheries_python" ]] || { echo 'Set FISHERIES_PYTHON to the prepared data environment.' >&2; exit 2; }
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-4}"
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
case "${1:-}" in
  pilot) config='config/east_asia_2012_pilot.json' ;;
  full) config='config/east_asia_2012_2024.json' ;;
  *) echo 'Usage: sbatch deploy/slurm/build_surface.sbatch {pilot|full}' >&2; exit 2 ;;
esac
"$fisheries_python" scripts/build_regional_dataset.py --config "$config" --mode toolbox
if [[ "${1}" == full ]]; then
  "$fisheries_python" scripts/validate_surface_release.py
fi
