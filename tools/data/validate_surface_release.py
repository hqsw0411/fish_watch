"""Read-only checks of the finished East Asia 2012-2024 surface release.

Does not attempt to restart the web app or alter the research database.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from tools.paths import ROOT
CONFIG = ROOT / 'config/east_asia_2012_2024.json'
FIELDS = ('sst_c', 'chl_mg_m3', 'npp_mg_c_m3_day', 'ssha_m')


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def validate() -> dict:
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    assert config['bbox'] == [115.0, 20.0, 135.0, 40.0]
    assert config['start_month'] == '2012-01'
    assert config['end_month'] == '2024-12'
    assert config['grid_degrees'] == 0.1
    assert {variable for item in config['environment_sources'] for variable in item['variables']} >= {
        'thetao', 'chl', 'nppv', 'sla'
    }
    releases_root = (ROOT / config['output_dir']).resolve()
    pointer_file = releases_root / 'latest.json'
    if not pointer_file.is_file():
        raise FileNotFoundError('No completed release: latest.json missing. Build has not finished.')
    pointer = json.loads(pointer_file.read_text(encoding='utf-8'))
    release = (releases_root / pointer['directory']).resolve()
    if not release.is_relative_to(releases_root) or not release.is_dir():
        raise ValueError('Unsafe or missing release directory')
    manifest = json.loads((release / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['id'] != 'east_asia_2012_2024':
        raise ValueError('Wrong data release')
    if manifest['bbox'] != [115.0, 20.0, 135.0, 40.0]:
        raise ValueError('Wrong bbox')
    if len(manifest['months']) != 156 or manifest['months'][0] != '2012-01' or manifest['months'][-1] != '2024-12':
        raise ValueError('The release is missing a calendar month')
    if manifest['rows'] != 6240000 or manifest['cells_per_month'] != 40000:
        raise ValueError('Unexpected grid-month count')
    if not manifest['sources']['gfw'].get('official_checksum_verified'):
        raise ValueError('Unverified GFW data')
    if len(manifest['sources']['gfw'].get('assets', [])) != 13:
        raise ValueError('Expected 13 GFW annual archives')
    if len(manifest['sources'].get('environment', [])) != 39:
        raise ValueError('Expected 3 environmental products per year for 13 years')
    coverage = manifest['quality']
    if set(coverage) != set(manifest['months']):
        raise ValueError('Incomplete monthly quality reports')
    for month in manifest['months']:
        counts = coverage[month]['valid_environment']
        if any(counts.get(field, 0) <= 0 for field in FIELDS):
            raise ValueError(f'No valid observations for at least one key field in {month}')
    sample = release / 'sample.csv'
    if file_sha256(sample) != manifest['sha256'] or pointer['sha256'] != manifest['sha256']:
        raise ValueError('CSV integrity check failed')
    indexed = release / 'sample.sqlite3'
    if not indexed.is_file():
        raise FileNotFoundError('Web SQLite index is missing; do not switch the site yet')
    con = sqlite3.connect(indexed.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        row_count, first_month, last_month = con.execute('SELECT COUNT(*), MIN(month), MAX(month) FROM cells').fetchone()
        if (row_count, first_month, last_month) != (6240000, '2012-01', '2024-12'):
            raise ValueError('Web SQLite has unexpected content')
        cols = {c[1] for c in con.execute('PRAGMA table_info(cells)')}
        if not set(FIELDS).issubset(cols):
            raise ValueError('Web SQLite is missing a required environment field')
    finally:
        con.close()
    return {
        'status': 'PASS', 'release': str(release),
        'months': 156, 'grid_month_rows': 6240000,
        'yearly_gfw_archives': 13, 'environmental_annual_files': 39,
        'surface_fields': list(FIELDS),
        'npp_units': 'mg C/m^3/day (surface volumetric nppv; not column integrated)',
        'remote_site_switched': False,
    }


if __name__ == '__main__':
    try:
        print(json.dumps(validate(), ensure_ascii=False, indent=2))
    except (AssertionError, KeyError, ValueError, FileNotFoundError, OSError) as exc:
        raise SystemExit(f'NOT READY: {exc}')
