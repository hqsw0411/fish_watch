"""Network-free integration tests for cross-year publication and monthly queries."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile

import runtime
sys.path.insert(0, str(runtime.ROOT))
import numpy as np
import pandas as pd
import xarray as xr

import build_regional_dataset as pipeline
import fetch_effort
from backend.app.store import select_cells, temperature_scale
from backend.app.temperature import derive, previous_calendar_month
from backend.app.main import app
from fastapi.testclient import TestClient


class RegionalDatasetTests(unittest.TestCase):
    def setUp(self):
        (runtime.ROOT / "tmp").mkdir(exist_ok=True)

    def test_downloads_verify_each_year_and_preserve_previous_files_on_failure(self):
        class Response(io.BytesIO):
            def __init__(self, data, status=200, headers=None):
                super().__init__(data)
                self.status = status
                self.headers = headers or {}
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / "tmp") as temporary:
            root = Path(temporary)
            assets = {f"fleet-monthly-csvs-10-v3-{year}.zip": f"official fixture {year}".encode()
                      for year in [2012, 2013]}
            record = {"files": [{"key": name, "size": len(data),
                                  "checksum": "md5:" + hashlib.md5(data).hexdigest()}
                                 for name, data in assets.items()]}
            config = {"gfw_record": "14982712", "start_month": "2012-12", "end_month": "2013-01",
                      "gfw_archive_pattern": "data/source/fleet-monthly-csvs-10-v3-{year}.zip"}
            previous = root / config["gfw_archive_pattern"].format(year=2013)
            previous.parent.mkdir(parents=True)
            previous.write_bytes(b"previous local copy")
            corrupt = True
            def open_fixture(url, timeout):
                if url == "https://zenodo.org/api/records/14982712":
                    return Response(json.dumps(record).encode())
                name = urlsplit(url.full_url).path.rsplit("/", 1)[1]
                data = assets[name]
                start, end = map(int, url.get_header("Range").removeprefix("bytes=").split("-"))
                headers = {"Content-Range": f"bytes {start}-{end}/{len(data)}"}
                segment = data[start:end + 1]
                if corrupt and "2013" in name:
                    segment = b"incomplete"
                return Response(segment, 206, headers)
            with patch.object(runtime, "ROOT", root), patch.object(fetch_effort, "urlopen", open_fixture), \
                 patch.object(fetch_effort, "MAX_RETRIES", 1):
                with self.assertRaisesRegex(RuntimeError, "Unable to download"):
                    fetch_effort.fetch(config)
                self.assertEqual(previous.read_bytes(), b"previous local copy")
                corrupt = False
                fetch_effort.fetch(config)
                for name, data in assets.items():
                    self.assertEqual((previous.parent / name).read_bytes(), data)

    def test_cross_year_publish_missing_values_and_resume(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / "tmp") as temporary:
            root = Path(temporary)
            config = json.loads((runtime.ROOT / "config/east_asia_2012_2024.json").read_text(encoding="utf-8"))
            config.update(id="fixture", bbox=[120., 30., 120.2, 30.2],
                          start_month="2012-12", end_month="2013-01",
                          output_dir="samples", initial_bbox=[120., 30., 120.2, 30.2])
            record = {"files": []}
            for year, month in [(2012, "2012-12"), (2013, "2013-01")]:
                archive = root / config["gfw_archive_pattern"].format(year=year)
                archive.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(f"fleet-monthly-csvs-10-v3-{month}-01.csv",
                        "date,cell_ll_lon,cell_ll_lat,hours,fishing_hours,geartype\n"
                        f"{month}-01,120,30,10,2,trawlers\n"
                        f"{month}-01,120.1,30,0,0,trawlers\n")
                record["files"].append({"key": archive.name,
                    "checksum": "md5:" + hashlib.md5(archive.read_bytes()).hexdigest()})
                for source in config["environment_sources"]:
                    file = root / source["path"].format(year=year)
                    file.parent.mkdir(parents=True, exist_ok=True)
                    fields = {}
                    for variable, field in source["variables"].items():
                        values = np.array([[[1., 2.], [3., np.nan]]]) + (year - 2012) * 10
                        unit = {"thetao": "degrees_C", "zos": "m", "sla": "m",
                                "chl": "mg m-3", "nppv": "mg m-3 day-1"}[variable]
                        fields[variable] = (("time", "latitude", "longitude"), values, {"units": unit})
                    dataset = xr.Dataset(fields, coords={"time": pd.to_datetime([month + "-01"]),
                                                         "latitude": [30.05, 30.15],
                                                         "longitude": [120.05, 120.15]})
                    dataset.to_netcdf(file, engine="h5netcdf")
                    audit = {"sha256": pipeline.digest(file), "dataset_id": source["dataset_id"],
                             "bbox_with_margin": [119.5, 29.5, 120.7, 30.7]}
                    file.with_suffix(".metadata.json").write_text(json.dumps(audit), encoding="utf-8")
            metadata = root / config["gfw_metadata"]
            metadata.parent.mkdir(parents=True, exist_ok=True)
            metadata.write_text(json.dumps(record), encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs/data_dictionary.json").write_bytes((runtime.ROOT / "docs/data_dictionary.json").read_bytes())
            with patch.object(runtime, "ROOT", root):
                partitions = []
                for year, month in [(2012, "2012-12"), (2013, "2013-01")]:
                    annual = pipeline.annual_config(config, year)
                    pipeline.check_environment(annual)
                    partition = pipeline.build_partition(annual, month, Path("samples"))
                    self.assertEqual(partition["rows"], 4)
                    partitions.append(partition)
                release = pipeline.publish(config, partitions)
                manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["months"], ["2012-12", "2013-01"])
                self.assertEqual(manifest["rows"], 8)
                self.assertEqual(len(manifest["sources"]["gfw"]["assets"]), 2)
                self.assertTrue(manifest["sources"]["gfw"]["official_checksum_verified"])
                self.assertEqual(pipeline.digest(release / "sample.csv"), manifest["sha256"])
                database = release / "sample.sqlite3"
                december = select_cells(database, "2012-12", tuple(config["bbox"]), 4)
                january = select_cells(database, "2013-01", tuple(config["bbox"]), 4)
                self.assertEqual(sorted(cell["effort_status"] for cell in december),
                                 ["no_record", "no_record", "observed", "reported_zero"])
                self.assertEqual(sum(cell["sst_c"] is None for cell in january), 1)
                self.assertEqual(previous_calendar_month("2013-01"), "2012-12")
                derived, _ = derive(january, december, "previous")
                self.assertEqual([cell["delta_sst_c"] for cell in derived if cell["delta_sst_c"] is not None], [10., 10., 10.])
                self.assertAlmostEqual(temperature_scale(database, "regional"), 1.)
                self.assertAlmostEqual(temperature_scale(database, "previous"), 10.)
                self.assertEqual(pipeline.publish(config, partitions), release)
                # A per-dataset limit must override the prototype default, and
                # the January endpoint must still reference December's values.
                with patch.dict(os.environ, {"FISHERIES_SAMPLE_DIR": str(release)}), \
                     patch("backend.app.main.MAX_REGION_CELLS", 1), TestClient(app) as client:
                    catalog = client.get("/api/catalog").json()
                    self.assertEqual(catalog["initial_bbox"], config["bbox"])
                    self.assertEqual(catalog["max_region_cells"], 40000)
                    response = client.get("/api/sample", params={"month": "2013-01"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.json()["cells"]), 4)
                    response = client.get("/api/temperature", params={"month": "2013-01", "baseline": "previous"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["reference"]["previous_month"], "2012-12")
                    self.assertEqual(response.json()["scale"]["max"], 10.)
                # A corrupted cached month must be rebuilt, never silently reused.
                cached = root / "samples/partitions/2013-01/sample.csv"
                cached.write_bytes(b"corrupt")
                rebuilt = pipeline.build_partition(pipeline.annual_config(config, 2013), "2013-01", Path("samples"))
                self.assertEqual(pipeline.digest(cached), rebuilt["sha256"])
                # A missing input month is rejected even when the file checksum is valid.
                invalid = pipeline.annual_config(config, 2013)
                invalid["start_month"] = "2013-01"
                invalid["end_month"] = "2013-02"
                with self.assertRaisesRegex(ValueError, "missing months"):
                    pipeline.check_environment(invalid)


if __name__ == "__main__":
    unittest.main()
