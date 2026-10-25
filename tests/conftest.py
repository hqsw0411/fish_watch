import csv
import hashlib
import json

import pytest
from backend.app import main
from backend.app.store import ensure_database


@pytest.fixture(autouse=True)
def clear_api_state():
    for fn in (main.sample_dir, main.load_manifest, main.temperature_monthly):
        fn.cache_clear()
    yield
    for fn in (main.sample_dir, main.load_manifest, main.temperature_monthly):
        fn.cache_clear()


@pytest.fixture
def sample_release(tmp_path):
    directory = tmp_path / 'release'
    directory.mkdir()
    dictionary = json.loads((main.ROOT / 'docs/data_dictionary.json').read_text(encoding='utf-8'))
    fields = [x['field'] for x in dictionary]
    rows = []
    for month, offset in [('2012-12', 0), ('2013-01', 10)]:
        for i, hours in enumerate([2, 0, None]):
            row = {f['field']: None if f['nullable'] else 0 if f['type'] != 'string' else 'fixture' for f in dictionary}
            row.update(month=month, cell_id=f'cell-{i}', cell_ll_lon=i*.1, cell_ll_lat=0,
                       longitude=i*.1+.05, latitude=.05, fishing_hours=hours, hours=hours,
                       source_row_count=0 if hours is None else 1,
                       effort_status=['observed','reported_zero','no_record'][i],
                       energy_status='not_available', sst_c=10+i+offset,
                       environment_status='partial_or_missing')
            rows.append(row)
    with (directory/'sample.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader();writer.writerows(rows)
    manifest = {'id':'synthetic-fixture', 'months':['2012-12','2013-01'], 'rows':6,
                'cells_per_month':3,'grid_degrees':.1,'bbox':[0,0,.3,.1],
                'initial_bbox':[0,0,.3,.1], 'environment_fields':['sst_c'],
                'sha256':hashlib.sha256((directory/'sample.csv').read_bytes()).hexdigest()}
    (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    ensure_database(directory,dictionary)
    return directory,dictionary,manifest
