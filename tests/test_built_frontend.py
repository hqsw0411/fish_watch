from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient
from backend.app import main


@pytest.mark.skipif(not (main.ROOT/'frontend/dist/index.html').is_file(),reason='Run npm run build for packaged UI integration')
def test_built_frontend_assets_and_offline_basemap(sample_release,monkeypatch):
    directory,_,_=sample_release
    monkeypatch.setenv('FISHERIES_SAMPLE_DIR',str(directory))
    with TestClient(main.app) as client:
        page=client.get('/')
        assert page.status_code==200 and '<div id="root">' in page.text
        assets=re.findall(r'(?:src|href)="(/assets/[^"]+)"',page.text)
        assert assets
        assert all(client.get(asset).status_code==200 for asset in assets)
        assert client.get('/land.geojson').json()['features']
