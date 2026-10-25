"""Build the normalized SQLite database defined by the data-integration plan.

The database is intentionally separate from the browser cache (sample.sqlite3).
It stores source provenance, dimensions, raw GFW fleet observations, aggregated
cell-period effort, normalized CMEMS observations, model features, and empty
model/prediction tables ready for later training runs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/week2_region.json"
DEFAULT_OUTPUT = ROOT / "data/database/fisheries_forecast.sqlite3"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE database_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE data_source (
    source_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    product_id TEXT,
    doi TEXT,
    source_url TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    license TEXT,
    native_resolution TEXT,
    coverage_start TEXT,
    coverage_end TEXT,
    retrieved_at TEXT NOT NULL,
    checksum TEXT,
    citation TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE source_asset (
    asset_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES data_source(source_id),
    uri_or_path TEXT NOT NULL,
    format TEXT NOT NULL,
    bbox_west REAL,
    bbox_south REAL,
    bbox_east REAL,
    bbox_north REAL,
    time_start TEXT,
    time_end TEXT,
    file_size INTEGER,
    sha256 TEXT,
    md5 TEXT,
    download_mode TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE variable_definition (
    variable_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    source_short_name TEXT,
    standard_name TEXT,
    unit TEXT,
    depth_policy TEXT,
    aggregation_policy TEXT,
    feature_group TEXT,
    description TEXT NOT NULL,
    definition_version TEXT NOT NULL
);

CREATE TABLE grid_definition (
    grid_id TEXT PRIMARY KEY,
    crs TEXT NOT NULL,
    dx REAL NOT NULL,
    dy REAL NOT NULL,
    origin_policy TEXT NOT NULL,
    cell_convention TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE grid_cell (
    cell_id TEXT PRIMARY KEY,
    grid_id TEXT NOT NULL REFERENCES grid_definition(grid_id),
    ll_lon REAL NOT NULL,
    ll_lat REAL NOT NULL,
    center_lon REAL NOT NULL,
    center_lat REAL NOT NULL,
    area_km2 REAL,
    land_mask INTEGER,
    bathymetry_m REAL
);

CREATE TABLE time_dimension (
    time_id TEXT PRIMARY KEY,
    time_start TEXT NOT NULL,
    time_end TEXT NOT NULL,
    granularity TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    forecast_origin TEXT,
    lead_period INTEGER,
    climatology_period TEXT
);

CREATE TABLE environment_observation (
    source_asset_id TEXT NOT NULL REFERENCES source_asset(asset_id),
    variable_id TEXT NOT NULL REFERENCES variable_definition(variable_id),
    grid_id TEXT NOT NULL REFERENCES grid_definition(grid_id),
    cell_id TEXT NOT NULL REFERENCES grid_cell(cell_id),
    time_id TEXT NOT NULL REFERENCES time_dimension(time_id),
    value REAL,
    depth_m REAL,
    valid_flag INTEGER NOT NULL CHECK(valid_flag IN (0, 1)),
    missing_reason TEXT,
    spatial_method TEXT NOT NULL,
    temporal_method TEXT NOT NULL,
    coverage_fraction REAL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_asset_id, variable_id, cell_id, time_id, depth_m)
);

CREATE TABLE effort_observation (
    dataset_version_id TEXT NOT NULL,
    time_id TEXT NOT NULL REFERENCES time_dimension(time_id),
    cell_id TEXT NOT NULL REFERENCES grid_cell(cell_id),
    flag_code TEXT NOT NULL,
    gear_type TEXT NOT NULL,
    hours REAL NOT NULL CHECK(hours >= 0),
    fishing_hours REAL NOT NULL CHECK(fishing_hours >= 0),
    mmsi_present INTEGER CHECK(mmsi_present IS NULL OR mmsi_present >= 0),
    source_asset_id TEXT NOT NULL REFERENCES source_asset(asset_id),
    source_row_count INTEGER NOT NULL DEFAULT 1 CHECK(source_row_count > 0),
    effort_status TEXT NOT NULL CHECK(effort_status IN ('observed', 'reported_zero')),
    quality_flags TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dataset_version_id, time_id, cell_id, flag_code, gear_type)
);

CREATE TABLE effort_cell_period (
    dataset_version_id TEXT NOT NULL,
    time_id TEXT NOT NULL REFERENCES time_dimension(time_id),
    cell_id TEXT NOT NULL REFERENCES grid_cell(cell_id),
    target_scope TEXT NOT NULL,
    hours REAL,
    fishing_hours REAL,
    source_row_count INTEGER NOT NULL,
    gear_types TEXT,
    effort_status TEXT NOT NULL CHECK(effort_status IN ('observed', 'reported_zero', 'no_record')),
    energy_kwh REAL,
    energy_status TEXT NOT NULL,
    aggregation_rule TEXT NOT NULL,
    PRIMARY KEY (dataset_version_id, time_id, cell_id, target_scope)
);

CREATE TABLE alignment_run (
    run_id TEXT PRIMARY KEY,
    grid_id TEXT NOT NULL REFERENCES grid_definition(grid_id),
    time_rule TEXT NOT NULL,
    spatial_method TEXT NOT NULL,
    depth_policy TEXT NOT NULL,
    input_asset_versions TEXT NOT NULL,
    program_version TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    result_sha256 TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE feature_definition (
    feature_id TEXT PRIMARY KEY,
    field_name TEXT NOT NULL UNIQUE,
    formula TEXT NOT NULL,
    unit TEXT NOT NULL,
    source_dependencies TEXT NOT NULL,
    spatial_window TEXT,
    temporal_window TEXT,
    lag_allowed INTEGER NOT NULL CHECK(lag_allowed IN (0, 1)),
    missing_policy TEXT NOT NULL,
    definition_version TEXT NOT NULL
);

CREATE TABLE feature_value (
    run_id TEXT NOT NULL REFERENCES alignment_run(run_id),
    feature_id TEXT NOT NULL REFERENCES feature_definition(feature_id),
    time_id TEXT NOT NULL REFERENCES time_dimension(time_id),
    cell_id TEXT NOT NULL REFERENCES grid_cell(cell_id),
    target_scope TEXT NOT NULL,
    value REAL,
    valid_flag INTEGER NOT NULL CHECK(valid_flag IN (0, 1)),
    missing_reason TEXT,
    PRIMARY KEY (run_id, feature_id, time_id, cell_id, target_scope)
);

CREATE TABLE model_run (
    model_run_id TEXT PRIMARY KEY,
    algorithm TEXT NOT NULL,
    target_definition TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    train_time_start TEXT,
    train_time_end TEXT,
    spatial_scope TEXT,
    forecast_lead_period INTEGER,
    cross_validation TEXT,
    random_seed INTEGER,
    code_version TEXT,
    data_fingerprint TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    model_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE prediction_result (
    model_run_id TEXT NOT NULL REFERENCES model_run(model_run_id),
    forecast_origin TEXT NOT NULL,
    target_time_id TEXT NOT NULL REFERENCES time_dimension(time_id),
    cell_id TEXT NOT NULL REFERENCES grid_cell(cell_id),
    target_scope TEXT NOT NULL,
    prediction REAL,
    lower_bound REAL,
    upper_bound REAL,
    unit TEXT NOT NULL,
    explanation_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (model_run_id, forecast_origin, target_time_id, cell_id, target_scope)
);

CREATE INDEX idx_environment_time_cell_variable
    ON environment_observation(time_id, cell_id, variable_id);
CREATE INDEX idx_environment_variable_time_cell
    ON environment_observation(variable_id, time_id, cell_id);
CREATE INDEX idx_effort_time_cell_gear_flag
    ON effort_observation(time_id, cell_id, gear_type, flag_code);
CREATE INDEX idx_effort_dataset_time_cell
    ON effort_observation(dataset_version_id, time_id, cell_id);
CREATE INDEX idx_effort_cell_period_time_cell_scope
    ON effort_cell_period(time_id, cell_id, target_scope);
CREATE INDEX idx_feature_run_time_cell_feature
    ON feature_value(run_id, time_id, cell_id, feature_id);
CREATE INDEX idx_grid_cell_grid_lon_lat
    ON grid_cell(grid_id, ll_lon, ll_lat);
CREATE INDEX idx_time_year_month
    ON time_dimension(year, month);
"""


NOW = datetime.now(timezone.utc).isoformat()
GRID_ID = "gfw_0p1deg_epsg4326"
ALIGNMENT_RUN = "align_week2_2012_h1_v1"
GFW_VERSION = "gfw_v3.0"
TARGET_SCOPE = "all_gear"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cell_id(lon: float, lat: float) -> str:
    return f"{lon:.4f}_{lat:.4f}"


def sqlite_value(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_sample(config: dict) -> pd.DataFrame:
    path = ROOT / config["output_dir"] / "sample.csv"
    if not path.is_file():
        raise FileNotFoundError(f"样例不存在，请先运行 scripts/build_sample.py: {path}")
    frame = pd.read_csv(path)
    required = {
        "month", "cell_id", "cell_ll_lon", "cell_ll_lat", "longitude", "latitude",
        "hours", "fishing_hours", "source_row_count", "gear_types", "effort_status",
        "energy_kwh", "energy_status", "sst_c", "chl_mg_m3", "npp_mg_c_m3_day",
        "ssh_m", "ssha_m", "environment_status",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"样例缺少字段: {missing}")
    if frame[["month", "cell_id"]].duplicated().any():
        raise ValueError("同一月份内存在重复的 month/cell_id 主键")
    return frame


def source_rows_from_zip(config: dict, frame: pd.DataFrame) -> list[tuple]:
    archive = ROOT / config["gfw_zip"]
    if not archive.is_file():
        raise FileNotFoundError(f"GFW 压缩包不存在: {archive}")
    west, south, east, north = map(float, config["bbox"])
    months = sorted(frame["month"].unique())
    result: list[tuple] = []
    with zipfile.ZipFile(archive) as bundle:
        for month in months:
            member = f"fleet-monthly-csvs-10-v3-{month}-01.csv"
            if member not in bundle.namelist():
                raise ValueError(f"GFW 压缩包缺少月份: {month}")
            with bundle.open(member) as handle:
                reader = csv.DictReader((line.decode("utf-8") for line in handle))
                required = {"date", "cell_ll_lon", "cell_ll_lat", "flag", "geartype", "hours", "fishing_hours", "mmsi_present"}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError(f"GFW 字段不完整: {member}")
                for row in reader:
                    lon = float(row["cell_ll_lon"])
                    lat = float(row["cell_ll_lat"])
                    if not (west <= lon < east and south <= lat < north):
                        continue
                    hours = float(row["hours"])
                    fishing = float(row["fishing_hours"])
                    if hours < 0 or fishing < 0 or fishing > hours + 1e-6:
                        raise ValueError(f"GFW 努力量值非法: {member} {row}")
                    flag = (row.get("flag") or "UNKNOWN").strip() or "UNKNOWN"
                    gear = (row.get("geartype") or "unknown").strip().lower() or "unknown"
                    mmsi = row.get("mmsi_present")
                    mmsi_value = int(float(mmsi)) if mmsi not in (None, "") else None
                    result.append((month, cell_id(lon, lat), flag, gear, hours, fishing, mmsi_value))
    if not result:
        raise ValueError("目标区域没有 GFW 原始记录")
    return result


def environment_sources(config: dict, manifest: dict, archive: Path) -> list[dict]:
    assets = []
    for source in manifest["sources"]["environment"]:
        asset_id = f"cmems_{source['dataset_id']}_{source.get('download', {}).get('dataset_version', 'unknown')}"
        assets.append({"asset_id": asset_id, "source": source})
    assets.append({"asset_id": "gfw_fleet_monthly_v3_2012", "source": {"path": str(archive.relative_to(ROOT))}})
    return assets


def register_sources(connection: sqlite3.Connection, config: dict, manifest: dict, sample: pd.DataFrame) -> dict[str, str]:
    archive = ROOT / config["gfw_zip"]
    gfw_record = json.loads((ROOT / "data/metadata/zenodo_14982712.record.json").read_text(encoding="utf-8"))
    gfw_source_id = "gfw_fleet_monthly_v3"
    connection.execute(
        "INSERT INTO data_source VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (gfw_source_id, "Global Fishing Watch", "Global AIS-based Apparent Fishing Effort Dataset", "GFW_FLEET_MONTHLY_10_V3", "10.5281/zenodo.14982712", "https://zenodo.org/records/14982712", "3.0", "CC BY-NC 4.0", "0.1 degree monthly", "2012-01-01", "2012-12-01", NOW, "md5:" + md5(archive), "Global Fishing Watch. 2025. Global AIS-based Apparent Fishing Effort Dataset, Version 3.0.", json.dumps({"record_id": 14982712, "title": gfw_record["metadata"]["title"]}, ensure_ascii=False)))
    source_asset_ids = {"gfw": "gfw_fleet_monthly_v3_2012"}
    connection.execute(
        "INSERT INTO source_asset VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("gfw_fleet_monthly_v3_2012", gfw_source_id, str(archive.relative_to(ROOT)), "zip", *config["bbox"], "2012-01-01", "2012-12-31", archive.stat().st_size, sha256(archive), md5(archive), "local", json.dumps({"months": sorted(sample.month.unique())})))

    catalog_map = {
        "cmems_mod_glo_phy_my_0.083deg_P1M-m": ("cmems_physics", "GLOBAL_MULTIYEAR_PHY_001_030", "10.48670/moi-00021", "202311", "https://s3.waw3-1.cloudferro.com/mdl-arco-geo-025/arco/GLOBAL_MULTIYEAR_PHY_001_030/cmems_mod_glo_phy_my_0.083deg_P1M-m_202311/geoChunked.zarr", "0.083 degree monthly", "E.U. Copernicus Marine Service Information"),
        "cmems_mod_glo_bgc_my_0.25deg_P1M-m": ("cmems_bgc", "GLOBAL_MULTIYEAR_BGC_001_029", "10.48670/moi-00019", "202406", "https://s3.waw3-1.cloudferro.com/mdl-arco-geo-018/arco/GLOBAL_MULTIYEAR_BGC_001_029/cmems_mod_glo_bgc_my_0.25deg_P1M-m_202406/geoChunked.zarr", "0.25 degree monthly", "E.U. Copernicus Marine Service Information"),
        "cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1M-m": ("cmems_altimetry", "SEALEVEL_GLO_PHY_L4_MY_008_047", "10.48670/moi-00148", "202411", "https://s3.waw3-1.cloudferro.com/mdl-arco-geo-045/arco/SEALEVEL_GLO_PHY_L4_MY_008_047/cmems_obs-sl_glo_phy-ssh_my_allsat-l4-duacs-0.125deg_P1M-m_202411/geoChunked.zarr", "0.125 degree monthly", "E.U. Copernicus Marine Service Information"),
    }
    env_asset_ids: dict[str, str] = {}
    for source in manifest["sources"]["environment"]:
        dataset = source["dataset_id"]
        source_id, product_id, doi, version, uri, resolution, provider = catalog_map[dataset]
        local_path = ROOT / source["path"]
        info = {"dataset_id": dataset, "dataset_version": version, "asset_uri": uri, "variables": source.get("variables", {})}
        connection.execute(
            "INSERT INTO data_source VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source_id, provider, dataset, product_id, doi, "https://data.marine.copernicus.eu/products", version, "Copernicus Marine Service Information", resolution, "1993-01-01", "2026-12-31", NOW, source.get("sha256"), f"Copernicus Marine {dataset}", json.dumps(info, ensure_ascii=False)))
        asset_id = f"cmems_{dataset}_{version}"
        config_source = next(item for item in config["environment_sources"] if item["dataset_id"] == dataset)
        env_asset_ids.update({field: asset_id for field in config_source["variables"].values()})
        last_period = pd.Period(sample.month.max(), freq="M")
        connection.execute(
            "INSERT INTO source_asset VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (asset_id, source_id, source["path"], "NetCDF", *config["bbox"], sample.month.min() + "-01", last_period.end_time.strftime("%Y-%m-%dT23:59:59Z"), local_path.stat().st_size if local_path.exists() else None, source.get("sha256"), None, source.get("download", {}).get("mode", "public"), json.dumps({"download": source.get("download", {}), "native_grid_degrees": source.get("native_grid_degrees")}, ensure_ascii=False)))
    source_asset_ids.update(env_asset_ids)
    return source_asset_ids


def insert_dimensions(connection: sqlite3.Connection, config: dict, sample: pd.DataFrame):
    connection.execute("INSERT INTO grid_definition VALUES (?,?,?,?,?,?,?)", (GRID_ID, config["crs"], config["grid_degrees"], config["grid_degrees"], "GFW lower-left origin", "cell lower-left coordinate; half-open bbox", "GFW monthly target grid"))
    cells = sample[["cell_id", "cell_ll_lon", "cell_ll_lat", "longitude", "latitude"]].drop_duplicates("cell_id")
    connection.executemany("INSERT INTO grid_cell VALUES (?,?,?,?,?,?,?, ?, ?)", [(row.cell_id, GRID_ID, row.cell_ll_lon, row.cell_ll_lat, row.longitude, row.latitude, None, None, None) for row in cells.itertuples()])
    for month in sorted(sample["month"].unique()):
        year, numeric = map(int, month.split("-"))
        start = pd.Timestamp(f"{month}-01")
        end = start + pd.offsets.MonthEnd(1)
        connection.execute("INSERT INTO time_dimension VALUES (?,?,?,?,?,?,?,?,?)", (month, start.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT23:59:59Z"), "month", year, numeric, None, None, month[5:]))


VARIABLES = {
    "sst_c": ("var_sst_c", "thetao", "sea_water_potential_temperature", "degrees_C", "surface model layer", "monthly mean", "physical", "CMEMS thetao surface proxy"),
    "chl_mg_m3": ("var_chl_mg_m3", "chl", "mass_concentration_of_chlorophyll_a_in_sea_water", "mg m-3", "surface model layer", "monthly mean", "biogeochemical", "CMEMS chlorophyll-a"),
    "npp_mg_c_m3_day": ("var_npp_mg_c_m3_day", "nppv", "net_primary_production_of_biomass_expressed_as_carbon_per_unit_volume_in_sea_water", "mg C m-3 day-1", "surface model layer", "monthly mean", "biogeochemical", "CMEMS volumetric net primary production"),
    "ssh_m": ("var_ssh_m", "zos", "sea_surface_height_above_geoid", "m", "surface", "monthly mean", "physical", "CMEMS sea surface height above geoid"),
    "ssha_m": ("var_ssha_m", "sla", "sea_surface_height_above_sea_level", "m", "surface", "monthly mean", "altimetry", "DUACS sea level anomaly, 1993-2012 reference period"),
}


FEATURES = {
    "sst_c": ("feature_sst_c", "raw aligned sst_c", "degrees_C", "sst_c", None, None, 1, "keep null"),
    "chl_mg_m3": ("feature_chl_mg_m3", "raw aligned chl_mg_m3", "mg m-3", "chl_mg_m3", None, None, 1, "keep null"),
    "log_chl": ("feature_log_chl", "log1p(chl_mg_m3)", "1", "chl_mg_m3", None, None, 1, "keep null"),
    "npp_mg_c_m3_day": ("feature_npp_mg_c_m3_day", "raw aligned npp_mg_c_m3_day", "mg C m-3 day-1", "npp_mg_c_m3_day", None, None, 1, "keep null"),
    "ssh_m": ("feature_ssh_m", "raw aligned ssh_m", "m", "ssh_m", None, None, 1, "keep null"),
    "ssha_m": ("feature_ssha_m", "raw aligned ssha_m", "m", "ssha_m", None, None, 1, "keep null"),
    "sst_grad_c_per_100km": ("feature_sst_grad", "sqrt((dSST/dx)^2+(dSST/dy)^2), normalized to 100 km", "degrees_C / 100 km", "sst_c", "3x3 local gradient", None, 0, "boundary null"),
    "ssh_grad_m_per_100km": ("feature_ssh_grad", "sqrt((dSSH/dx)^2+(dSSH/dy)^2), normalized to 100 km", "m / 100 km", "ssh_m", "3x3 local gradient", None, 0, "boundary null"),
    "sst_mof_c_per_100km": ("feature_sst_mof", "multi-scale Sobel magnitude with n=5", "degrees_C / 100 km", "sst_c", "n=5, approximately 100 km", None, 0, "boundary null"),
    "ssh_mof_m_per_100km": ("feature_ssh_mof", "multi-scale Sobel magnitude with n=5", "m / 100 km", "ssh_m", "n=5, approximately 100 km", None, 0, "boundary null"),
}


def register_variables(connection: sqlite3.Connection):
    for name, values in VARIABLES.items():
        variable_id, short, standard, unit, depth, aggregation, group, description = values
        connection.execute("INSERT INTO variable_definition VALUES (?,?,?,?,?,?,?,?,?,?)", (variable_id, name, short, standard, unit, depth, aggregation, group, description, "v1"))
    for name, values in FEATURES.items():
        feature_id, formula, unit, deps, spatial, temporal, lag_allowed, missing = values
        connection.execute("INSERT INTO feature_definition VALUES (?,?,?,?,?,?,?,?,?,?)", (feature_id, name, formula, unit, deps, spatial, temporal, lag_allowed, missing, "v1"))


def insert_effort(connection: sqlite3.Connection, rows: list[tuple], source_asset_id: str):
    values = []
    for month, cid, flag, gear, hours, fishing, mmsi in rows:
        status = "reported_zero" if fishing == 0 else "observed"
        values.append((GFW_VERSION, month, cid, flag, gear, hours, fishing, mmsi, source_asset_id, 1, status, None, NOW))
    connection.executemany("INSERT INTO effort_observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values)


def insert_effort_aggregates(connection: sqlite3.Connection, sample: pd.DataFrame):
    values = []
    for row in sample.itertuples(index=False):
        values.append((GFW_VERSION, row.month, row.cell_id, TARGET_SCOPE, sqlite_value(row.hours), sqlite_value(row.fishing_hours), int(row.source_row_count), sqlite_value(row.gear_types), row.effort_status, sqlite_value(row.energy_kwh), row.energy_status, "sum flag/geartype within cell and month"))
    connection.executemany("INSERT INTO effort_cell_period VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)


def insert_environment(connection: sqlite3.Connection, sample: pd.DataFrame, source_asset_ids: dict[str, str]):
    values = []
    for field, (variable_id, *_rest) in VARIABLES.items():
        asset_id = source_asset_ids[field]
        for row in sample.itertuples(index=False):
            value = sqlite_value(getattr(row, field))
            valid = int(value is not None)
            depth = 0.494 if field == "sst_c" else (0.506 if field in {"chl_mg_m3", "npp_mg_c_m3_day"} else 0.0)
            values.append((asset_id, variable_id, GRID_ID, row.cell_id, row.month, value, depth, valid, None if valid else "source_missing", "nearest source-cell centre", "same calendar month; monthly source record", 1.0 if valid else 0.0, NOW))
    connection.executemany("INSERT INTO environment_observation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", values)


def grids_from_sample(sample: pd.DataFrame, field: str, month: str) -> tuple[np.ndarray, list[str], list[str]]:
    subset = sample[sample.month == month]
    lats = sorted(subset.cell_ll_lat.unique())
    lons = sorted(subset.cell_ll_lon.unique())
    values = np.full((len(lats), len(lons)), np.nan, dtype=float)
    ids = [[None for _ in lons] for _ in lats]
    lat_index = {value: index for index, value in enumerate(lats)}
    lon_index = {value: index for index, value in enumerate(lons)}
    for row in subset.itertuples(index=False):
        value = getattr(row, field)
        values[lat_index[row.cell_ll_lat], lon_index[row.cell_ll_lon]] = value if pd.notna(value) else np.nan
        ids[lat_index[row.cell_ll_lat]][lon_index[row.cell_ll_lon]] = row.cell_id
    return values, [cid for line in ids for cid in line], lats


def gradient_features(sample: pd.DataFrame, field: str, scale: float = 100.0, n: int = 5) -> dict[tuple[str, str], float | None]:
    result: dict[tuple[str, str], float | None] = {}
    for month in sorted(sample.month.unique()):
        subset = sample[sample.month == month]
        lats = sorted(subset.cell_ll_lat.unique())
        lons = sorted(subset.cell_ll_lon.unique())
        lat_index = {value: index for index, value in enumerate(lats)}
        lon_index = {value: index for index, value in enumerate(lons)}
        array = np.full((len(lats), len(lons)), np.nan)
        ids = {}
        for row in subset.itertuples(index=False):
            array[lat_index[row.cell_ll_lat], lon_index[row.cell_ll_lon]] = getattr(row, field)
            ids[(row.cell_ll_lat, row.cell_ll_lon)] = row.cell_id
        dy_km = 111.32 * float(np.median(np.diff(lats)))
        dx_km = 111.32 * math.cos(math.radians(float(np.mean(lats)))) * float(np.median(np.diff(lons)))
        for i, lat in enumerate(lats):
            for j, lon in enumerate(lons):
                cid = ids[(lat, lon)]
                if i == 0 or j == 0 or i == len(lats) - 1 or j == len(lons) - 1:
                    result[(month, cid)] = None
                    continue
                if not np.isfinite(array[i - 1:i + 2, j - 1:j + 2]).all():
                    result[(month, cid)] = None
                    continue
                gx = (array[i, j + 1] - array[i, j - 1]) / (2 * dx_km) * scale
                gy = (array[i + 1, j] - array[i - 1, j]) / (2 * dy_km) * scale
                result[(month, cid)] = float(math.hypot(gx, gy))
    return result


def mof_features(sample: pd.DataFrame, field: str, scale: float = 100.0, n: int = 5) -> dict[tuple[str, str], float | None]:
    result: dict[tuple[str, str], float | None] = {}
    for month in sorted(sample.month.unique()):
        subset = sample[sample.month == month]
        lats = sorted(subset.cell_ll_lat.unique())
        lons = sorted(subset.cell_ll_lon.unique())
        lat_index = {value: index for index, value in enumerate(lats)}
        lon_index = {value: index for index, value in enumerate(lons)}
        array = np.full((len(lats), len(lons)), np.nan)
        ids = {}
        for row in subset.itertuples(index=False):
            array[lat_index[row.cell_ll_lat], lon_index[row.cell_ll_lon]] = getattr(row, field)
            ids[(row.cell_ll_lat, row.cell_ll_lon)] = row.cell_id
        dy_km = 111.32 * float(np.median(np.diff(lats)))
        dx_km = 111.32 * math.cos(math.radians(float(np.mean(lats)))) * float(np.median(np.diff(lons)))
        for i, lat in enumerate(lats):
            for j, lon in enumerate(lons):
                cid = ids[(lat, lon)]
                if i < n or j < n or i >= len(lats) - n or j >= len(lons) - n:
                    result[(month, cid)] = None
                    continue
                patch = array[np.ix_([i - n, i, i + n], [j - n, j, j + n])]
                if not np.isfinite(patch).all():
                    result[(month, cid)] = None
                    continue
                gx = (-patch[0, 0] + patch[0, 2] - 2 * patch[1, 0] + 2 * patch[1, 2] - patch[2, 0] + patch[2, 2]) / 4
                gy = (patch[0, 0] + 2 * patch[0, 1] + patch[0, 2] - patch[2, 0] - 2 * patch[2, 1] - patch[2, 2]) / 4
                distance_x = 2 * n * dx_km
                distance_y = 2 * n * dy_km
                result[(month, cid)] = float(math.hypot(gx / distance_x, gy / distance_y) * scale)
    return result


def insert_features(connection: sqlite3.Connection, sample: pd.DataFrame):
    feature_rows = []
    direct_fields = ["sst_c", "chl_mg_m3", "npp_mg_c_m3_day", "ssh_m", "ssha_m"]
    gradients = {"sst_grad_c_per_100km": gradient_features(sample, "sst_c"), "ssh_grad_m_per_100km": gradient_features(sample, "ssh_m")}
    mofs = {"sst_mof_c_per_100km": mof_features(sample, "sst_c"), "ssh_mof_m_per_100km": mof_features(sample, "ssh_m")}
    for row in sample.itertuples(index=False):
        key = (row.month, row.cell_id)
        for field in direct_fields:
            value = sqlite_value(getattr(row, field))
            feature_id = FEATURES[field][0]
            feature_rows.append((ALIGNMENT_RUN, feature_id, row.month, row.cell_id, TARGET_SCOPE, value, int(value is not None), None if value is not None else "source_missing"))
        chl = sqlite_value(row.chl_mg_m3)
        value = math.log1p(chl) if chl is not None else None
        feature_rows.append((ALIGNMENT_RUN, FEATURES["log_chl"][0], row.month, row.cell_id, TARGET_SCOPE, value, int(value is not None), None if value is not None else "source_missing"))
        for field, values in {**gradients, **mofs}.items():
            value = values[key]
            feature_rows.append((ALIGNMENT_RUN, FEATURES[field][0], row.month, row.cell_id, TARGET_SCOPE, value, int(value is not None), None if value is not None else "boundary_or_missing"))
    connection.executemany("INSERT INTO feature_value VALUES (?,?,?,?,?,?,?,?)", feature_rows)


def write_database(config_path: Path, output: Path):
    config = load_config(config_path)
    sample = read_sample(config)
    manifest = json.loads((ROOT / config["output_dir"] / "manifest.json").read_text(encoding="utf-8"))
    archive = ROOT / config["gfw_zip"]
    raw_effort = source_rows_from_zip(config, sample)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    try:
        with closing(sqlite3.connect(output)) as connection:
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("INSERT INTO database_metadata VALUES (?,?)", ("schema_version", "1.0"))
            connection.execute("INSERT INTO database_metadata VALUES (?,?)", ("built_at", NOW))
            connection.execute("INSERT INTO database_metadata VALUES (?,?)", ("sample_id", manifest["id"]))
            connection.execute("INSERT INTO database_metadata VALUES (?,?)", ("sample_sha256", manifest["sha256"]))
            connection.execute("INSERT INTO database_metadata VALUES (?,?)", ("gfw_archive_sha256", sha256(archive)))
            connection.execute("INSERT INTO database_metadata VALUES (?,?)", ("row_count", str(len(sample))))
            source_ids = register_sources(connection, config, manifest, sample)
            insert_dimensions(connection, config, sample)
            register_variables(connection)
            insert_effort(connection, raw_effort, source_ids["gfw"])
            insert_effort_aggregates(connection, sample)
            insert_environment(connection, sample, source_ids)
            params = {"sample_id": manifest["id"], "target_scope": TARGET_SCOPE, "grid_degrees": config["grid_degrees"], "time_method": manifest["time_method"], "spatial_method": manifest["spatial_method"], "raw_effort_rows": len(raw_effort)}
            connection.execute("INSERT INTO alignment_run VALUES (?,?,?,?,?,?,?,?,?,?)", (ALIGNMENT_RUN, GRID_ID, "same calendar month", "nearest source-cell centre", "surface model layer", json.dumps({"gfw": GFW_VERSION, "environment": manifest["sources"]["environment"]}, ensure_ascii=False), "build_database.py v1", json.dumps(params, ensure_ascii=False), None, NOW))
            insert_features(connection, sample)
            connection.execute("ANALYZE")
            connection.commit()
    finally:
        pass


def verify_database(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        tables = [row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
        indexes = [dict(row) for row in connection.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        checks = {
            "effort_matches_sample": connection.execute("SELECT ABS(SUM(fishing_hours) - (SELECT SUM(fishing_hours) FROM effort_cell_period)) < 0.001 FROM effort_observation").fetchone()[0],
            "environment_rows": connection.execute("SELECT COUNT(*) = 14400 * 5 FROM environment_observation").fetchone()[0],
            "feature_rows": connection.execute("SELECT COUNT(*) = 14400 * 10 FROM feature_value").fetchone()[0],
            "no_negative_effort": connection.execute("SELECT COUNT(*) = 0 FROM effort_observation WHERE hours < 0 OR fishing_hours < 0").fetchone()[0],
        }
        plan = connection.execute("EXPLAIN QUERY PLAN SELECT * FROM effort_cell_period WHERE time_id=? AND cell_id BETWEEN ? AND ?", ("2012-01", "-62.0000_-48.0000", "-61.0000_-48.0000")).fetchall()
    return {"tables": tables, "counts": counts, "indexes": indexes, "checks": checks, "query_plan": [tuple(row) for row in plan]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    write_database(args.config if args.config.is_absolute() else ROOT / args.config, output)
    report = verify_database(output)
    print(json.dumps({"database": str(output), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
