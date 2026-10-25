import hashlib
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from backend.app import main
from backend.app.paths import resolve_sample_dir
from backend.app.store import ensure_database


def fingerprint(directory):
    return {p.name:(hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns)
            for p in directory.iterdir() if p.is_file()}


def test_api_readonly_null_zero_and_cross_year(sample_release,monkeypatch):
    directory,_,_=sample_release
    monkeypatch.setenv('FISHERIES_SAMPLE_DIR',str(directory))
    before=fingerprint(directory)
    with TestClient(main.app) as client:
        assert client.get('/api/health').json()['rows']==6
        response=client.get('/api/sample',params={'month':'2013-01'})
        assert response.status_code==200
        result=response.json()
        assert [c['fishing_hours'] for c in result['cells']]==[2,0,None]
        assert result['summary']['observed_cells']==2
        assert result['summary']['fishing_hours']==2
        assert client.get('/api/sample',params={'month':'2000-01'}).status_code==404
        assert client.get('/api/sample',params={'month':'2013-01','west':0}).status_code==422
        temp=client.get('/api/temperature',params={'month':'2013-01','baseline':'previous'}).json()
        assert temp['reference']['previous_month']=='2012-12'
        assert [c['delta_sst_c'] for c in temp['cells']]==[10,10,10]
    assert fingerprint(directory)==before


def test_missing_index_never_created_by_default(sample_release,monkeypatch):
    directory,dictionary,_=sample_release
    (directory/'sample.sqlite3').unlink()
    before=fingerprint(directory)
    with pytest.raises(RuntimeError,match='Published SQLite index'):
        ensure_database(directory,dictionary,allow_rebuild=False)
    assert fingerprint(directory)==before
    monkeypatch.setenv('FISHERIES_SAMPLE_DIR',str(directory))
    monkeypatch.delenv('FISHERIES_ALLOW_INDEX_BUILD',raising=False)
    with pytest.raises(RuntimeError,match='Published SQLite index'):
        with TestClient(main.app):pass
    assert fingerprint(directory)==before


def test_index_mismatch_refused_without_rewrite(sample_release):
    directory,dictionary,manifest=sample_release
    manifest['sha256']='0'*64
    (directory/'manifest.json').write_text(json.dumps(manifest))
    before=fingerprint(directory)
    with pytest.raises(RuntimeError):ensure_database(directory,dictionary,allow_rebuild=False)
    assert fingerprint(directory)==before


def test_pointer_is_anchored_and_cannot_escape(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/patagonia_2012_h1.json').write_text('{"output_dir":"releases"}')
    releases=tmp_path/'releases';releases.mkdir()
    release=releases/'v1';release.mkdir();(release/'manifest.json').write_text('{}')
    pointer=releases/'latest.json';pointer.write_text('{"directory":"v1"}')
    monkeypatch.delenv('FISHERIES_SAMPLE_DIR',raising=False)
    monkeypatch.delenv('FISHERIES_CONFIG',raising=False)
    assert resolve_sample_dir(tmp_path)==release.resolve()
    pointer.write_text('{"directory":"../outside"}')
    with pytest.raises(ValueError,match='escapes'):resolve_sample_dir(tmp_path)


def test_large_release_temperature_reads_only_needed_months(sample_release,monkeypatch):
    directory,_,manifest=sample_release
    manifest.update(max_region_cells=40000,rows=6240000,
                    temperature_scales={'previous':10,'regional':1})
    (directory/'manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setenv('FISHERIES_SAMPLE_DIR',str(directory))
    # No lifespan: this test isolates bounded query planning using the fixture DB.
    with patch.object(main,'select_cells',wraps=main.select_cells) as query:
        response=main.temperature(month='2013-01',baseline='previous',percentile=75,
                                  west=None,south=None,east=None,north=None)
    assert [c.args[1] for c in query.call_args_list]==['2013-01','2012-12']
    assert response['scale']=={'min':-10,'max':10}
    assert main.catalog()['max_region_cells']==40000


def test_missing_model_returns_service_unavailable(sample_release,monkeypatch,tmp_path):
    directory,_,_=sample_release
    monkeypatch.setenv('FISHERIES_SAMPLE_DIR',str(directory))
    monkeypatch.setenv('FISHERIES_MODEL_DIR',str(tmp_path/'missing-model'))
    params=dict(lat=0,lon=0,month=1,sst_c=10,chl_mg_m3=1,npp_mg_c_m3_day=1,ssh_m=0,ssha_m=0)
    with TestClient(main.app) as client:
        assert client.get('/api/predict',params=params).status_code==503
