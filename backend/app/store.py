"""Stream sample ingestion once; bounded, indexed SQL queries during browsing."""
from __future__ import annotations
import csv
from contextlib import closing, contextmanager
import hashlib
import json
import math
import re
import sqlite3
import tempfile
from pathlib import Path


@contextmanager
def connect_readonly(database: Path):
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def ensure_database(directory: Path, dictionary: list[dict], *, allow_rebuild: bool = True) -> Path:
    """Rebuild only when the published sample SHA changes; memory stays bounded."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    schema_hash = hashlib.sha256(json.dumps([(item["field"], item["type"], item["nullable"]) for item in dictionary]).encode()).hexdigest()
    database = directory / "sample.sqlite3"
    if database.is_file():
        try:
            with connect_readonly(database) as connection:
                fingerprints = dict(connection.execute("SELECT key,value FROM metadata").fetchall())
            if (fingerprints.get("csv_sha256") == manifest["sha256"] and fingerprints.get("schema_sha256") == schema_hash
                    and fingerprints.get("rows") == str(manifest["rows"])):
                return database
        except sqlite3.DatabaseError:
            pass
    if not allow_rebuild:
        raise RuntimeError(
            "Published SQLite index is missing or differs from its manifest. "
            "Build and validate a separate release before starting the API. "
            "FISHERIES_ALLOW_INDEX_BUILD=1 is for a writable development copy only."
        )
    digest = hashlib.sha256()
    with (directory / "sample.csv").open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != manifest["sha256"]:
        raise ValueError("CSV checksum differs from the published manifest")
    fields = [item["field"] for item in dictionary]
    if not all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in fields):
        raise ValueError("Invalid dictionary field identifier")
    sql_types = {"string": "TEXT", "number": "REAL", "integer": "INTEGER"}
    definitions = ", ".join(f'"{item["field"]}" {sql_types[item["type"]]}' for item in dictionary)
    columns = ",".join(f'"{name}"' for name in fields)
    temporary_handle = tempfile.NamedTemporaryFile(dir=directory, suffix=".sqlite3.tmp", delete=False)
    temporary = Path(temporary_handle.name)
    temporary_handle.close()
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute(f"CREATE TABLE cells ({definitions}, PRIMARY KEY (month, cell_id))")
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            with (directory / "sample.csv").open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if len(reader.fieldnames or []) != len(fields) or set(reader.fieldnames or []) != set(fields):
                    raise ValueError("CSV fields differ from dictionary")
                def records():
                    for row in reader:
                        record = []
                        for field in dictionary:
                            value = row[field["field"]]
                            if value == "":
                                if not field["nullable"]:
                                    raise ValueError(f"Missing non-null field: {field['field']}")
                                record.append(None)
                            elif field["type"] in {"number", "integer"}:
                                numeric = float(value)
                                if not math.isfinite(numeric):
                                    raise ValueError("Non-finite sample value")
                                record.append(int(numeric) if field["type"] == "integer" else numeric)
                            else:
                                record.append(value)
                        yield record
                connection.executemany(f"INSERT INTO cells ({columns}) VALUES ({','.join('?' for _ in fields)})", records())
            count = connection.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
            if count != manifest["rows"]:
                raise ValueError("Sample row count differs from manifest")
            connection.execute("CREATE INDEX idx_month_lon_lat ON cells(month, longitude, latitude)")
            connection.executemany("INSERT INTO metadata VALUES (?,?)", [("csv_sha256", manifest["sha256"]), ("schema_sha256", schema_hash), ("rows", str(count))])
            connection.execute("ANALYZE")
            connection.commit()
        temporary.replace(database)
    finally:
        if temporary.exists():
            temporary.unlink()
    return database


def select_cells(database: Path, month: str, bbox: tuple[float, float, float, float], limit: int) -> list[dict]:
    west, south, east, north = bbox
    # SQL applies both spatial bounds before returning rows. The limit also
    # bounds memory for exceptionally broad selections in later datasets.
    with connect_readonly(database) as connection:
        rows = connection.execute(
            "SELECT * FROM cells INDEXED BY idx_month_lon_lat "
            "WHERE month=? AND longitude>=? AND longitude<? AND latitude>=? AND latitude<? "
            "ORDER BY longitude, latitude LIMIT ?", (month, west, east, south, north, limit + 1))
        return [dict(row) for row in rows]


def temperature_scale(database: Path, baseline: str) -> float:
    """Fixed scale over all months, calculated in SQLite without loading rows."""
    with connect_readonly(database) as connection:
        if baseline == "regional":
            query = """WITH means AS (SELECT month, AVG(sst_c) AS mean FROM cells GROUP BY month)
                       SELECT MAX(ABS(c.sst_c - m.mean)) FROM cells c JOIN means m ON c.month=m.month"""
        elif baseline == "previous":
            query = """SELECT MAX(ABS(c.sst_c - p.sst_c)) FROM cells c JOIN cells p
                       ON p.cell_id=c.cell_id
                       AND p.month=strftime('%Y-%m', c.month || '-01', '-1 month')"""
        else:
            raise ValueError("Unknown temperature baseline")
        return float(connection.execute(query).fetchone()[0] or 1.)
