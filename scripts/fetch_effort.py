"""Download monthly 0.1-degree GFW ZIP files from Zenodo, with safe resume.

Uses the official record's file sizes and MD5 checksums; preserves verified
archives and unfinished .zip.download files across failed Slurm jobs.

This is a drop-in replacement for scripts/fetch_effort.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import runtime

# Range segments are intentionally below 4 MiB to tolerate connections that
# terminate large HTTP responses. Keeping only one download active also makes
# network usage friendlier to shared HPC infrastructure.
CHUNK_SIZE = 2 * 1024 * 1024
MAX_RETRIES = 6
CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$")


def md5(path: Path) -> str:
    result = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def years(config: dict) -> range:
    first = int(config["start_month"][:4])
    last = int(config["end_month"][:4])
    if first > last:
        raise ValueError("start_month must not be after end_month")
    return range(first, last + 1)


def _request(url: str, offset: int, end: int) -> Request:
    return Request(url, headers={
        "Range": f"bytes={offset}-{end}",
        "Accept-Encoding": "identity",
        "User-Agent": "fisheries-research/1.0",
    })


def _download_one(url: str, partial: Path, expected_size: int, name: str) -> None:
    if expected_size <= 0:
        raise ValueError(f"Invalid official size for {name}: {expected_size}")
    if partial.exists() and partial.stat().st_size > expected_size:
        raise ValueError(f"Partial file exceeds official size: {partial}")

    while True:
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == expected_size:
            return
        end = min(expected_size - 1, offset + CHUNK_SIZE - 1)
        needed = end - offset + 1
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with urlopen(_request(url, offset, end), timeout=90) as response:
                    status = response.status
                    if status == 206:
                        match = CONTENT_RANGE.match(response.headers.get("Content-Range", ""))
                        if not match or tuple(map(int, match.groups())) != (offset, end, expected_size):
                            raise ValueError(
                                f"Invalid Content-Range for {name}: "
                                f"{response.headers.get('Content-Range')!r}; "
                                f"expected bytes {offset}-{end}/{expected_size}"
                            )
                        data = response.read(needed + 1)
                        if len(data) != needed:
                            raise IOError(
                                f"Short/long HTTP 206 segment for {name}: "
                                f"got {len(data)} bytes, expected {needed}"
                            )
                        with partial.open("ab") as output:
                            output.write(data)
                    elif status == 200 and offset == 0:
                        # Some mirrors ignore Range but stream a full 200 response.
                        # Preserve any received prefix; attempt Range resume if EOF
                        # arrives early. Never mark it valid until size+MD5 pass.
                        received = 0
                        with partial.open("wb") as output:
                            while data := response.read(CHUNK_SIZE):
                                output.write(data)
                                received += len(data)
                                if received > expected_size:
                                    raise ValueError(f"HTTP 200 exceeds official size for {name}")
                        if received < expected_size:
                            print(f"Short HTTP 200 response for {name}: "
                                  f"{received}/{expected_size}; attempting resume", flush=True)
                    else:
                        raise RuntimeError(
                            f"Range request was ignored for {name} at offset {offset} "
                            f"(HTTP {status}); cannot safely resume. "
                            "Try a network/mirror that supports HTTP Range."
                        )
                break
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
                if attempt == MAX_RETRIES:
                    raise RuntimeError(
                        f"Unable to download {name} at byte {offset} after "
                        f"{MAX_RETRIES} attempts; partial kept at {partial}"
                    ) from exc
                seconds = min(2**attempt, 30)
                print(f"Retry {attempt}/{MAX_RETRIES} for {name} at byte {offset}: "
                      f"{exc}; sleeping {seconds}s", flush=True)
                time.sleep(seconds)
        current = partial.stat().st_size
        if current == offset:
            raise RuntimeError(f"No download progress for {name} at byte {offset}")
        if current == expected_size or current // (20 * 1024 * 1024) != offset // (20 * 1024 * 1024):
            print(f"Downloading {name}: {current:,}/{expected_size:,} bytes "
                  f"({current / expected_size:.1%})", flush=True)


def fetch(config: dict) -> None:
    record_id = config["gfw_record"]
    endpoint = f"https://zenodo.org/api/records/{record_id}"
    with urlopen(endpoint, timeout=45) as response:
        record = json.load(response)

    metadata = runtime.ROOT / config.get(
        "gfw_metadata", f"data/metadata/zenodo_{record_id}.record.json")
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    files = {item["key"]: item for item in record["files"]}

    pattern = config.get("gfw_archive_pattern")
    if pattern:
        requested = [
            (year, pattern.format(year=year), f"fleet-monthly-csvs-10-v3-{year}.zip")
            for year in years(config)
        ]
    else:
        destination = runtime.ROOT / config["gfw_zip"]
        requested = [(int(config["start_month"][:4]),
                      str(destination.relative_to(runtime.ROOT)), destination.name)]

    # Download sequentially: avoids simultaneous large requests, and failures
    # don't erase valid ZIPs or already received partial bytes.
    for year, relative, name in requested:
        entry = files.get(name)
        if entry is None:
            raise ValueError(f"Zenodo record has no archive for {year}: {name}")
        expected_size = int(entry["size"])
        checksum = str(entry["checksum"]).lower()
        if not re.fullmatch(r"md5:[0-9a-f]{32}", checksum):
            raise ValueError(f"Unexpected checksum format for {name}: {checksum}")

        destination = runtime.ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_file() and destination.stat().st_size == expected_size \
                and "md5:" + md5(destination) == checksum:
            print("Verified existing official archive", destination, flush=True)
            continue

        partial = destination.with_suffix(destination.suffix + ".download")
        # A retained .download might already be complete after a prior failure.
        if partial.is_file() and partial.stat().st_size == expected_size \
                and "md5:" + md5(partial) != checksum:
            raise ValueError(f"Completed partial has incorrect official MD5: {partial}. "
                             "Move it aside and retry, do not publish it.")
        url = f"https://zenodo.org/records/{record_id}/files/{name}?download=1"
        _download_one(url, partial, expected_size, name)
        if "md5:" + md5(partial) != checksum:
            raise ValueError(f"Official Zenodo MD5 mismatch for {name}; "
                             f"unverified partial preserved at {partial}")
        partial.replace(destination)
        print("Downloaded and verified", destination, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        default=runtime.ROOT / "config/sample_region.json")
    args = parser.parse_args()
    config_path = args.config if args.config.is_absolute() else runtime.ROOT / args.config
    fetch(json.loads(config_path.read_text(encoding="utf-8")))
