"""Validate immutable published data and supervise the existing web API.

Runs inside the Slurm allocation. No research database is modified.
Publish complete releases then atomically update latest.json; never edit an
active release in place. The supervisor checks the pointer every 600 seconds.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request


def validate_release(root, dictionary=None):
    root=Path(root).resolve()
    pointer=json.loads((root/'latest.json').read_text(encoding='utf-8'))
    directory=(root/pointer['directory']).resolve()
    if root not in directory.parents or not directory.is_dir():
        raise ValueError('Release pointer escapes publication directory')
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if not manifest['months'] or manifest['rows'] <= 0 or manifest['grid_degrees'] != .1:
        raise ValueError('Invalid release manifest')
    digest=hashlib.sha256()
    with (directory/'sample.csv').open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):digest.update(block)
    if digest.hexdigest()!=manifest['sha256'] or pointer['sha256']!=manifest['sha256']:
        raise ValueError('Published CSV checksum mismatch')
    with closing(sqlite3.connect((directory/'sample.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Corrupt SQLite')
        metadata=dict(c.execute('SELECT key,value FROM metadata'))
        if dictionary is not None:
            schema=hashlib.sha256(json.dumps([(i['field'],i['type'],i['nullable']) for i in dictionary]).encode()).hexdigest()
            if metadata.get('schema_sha256')!=schema:raise ValueError('Index schema differs from running API')
        if metadata.get('csv_sha256')!=manifest['sha256'] or metadata.get('rows')!=str(manifest['rows']):
            raise ValueError('Index does not match published manifest')
        count,first,last=c.execute('SELECT count(*),min(month),max(month) FROM cells').fetchone()
        if (count,first,last)!=(manifest['rows'],min(manifest['months']),max(manifest['months'])):
            raise ValueError('Index coverage differs from manifest')
    return directory,manifest


def run_api(app_root,directory,port):
    env={**os.environ,'FISHERIES_SAMPLE_DIR':str(directory)}
    return subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','0.0.0.0','--port',str(port),'--workers','1'],cwd=app_root,env=env)


def healthy(child,port,manifest):
    for _ in range(60):
        if child.poll() is not None:return False
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=2) as r:
                data=json.load(r)
            if data.get('sample_id')==manifest['id'] and data.get('rows')==manifest['rows']:return True
        except (OSError,ValueError):pass
        time.sleep(1)
    return False


def stop(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:child.wait(timeout=20)
        except subprocess.TimeoutExpired:child.kill();child.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--releases',type=Path,required=True)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if args.check:
        d,m=validate_release(args.releases)
        print(json.dumps({'status':'PASS','release':str(d),'rows':m['rows'],'months':len(m['months'])}));return
    state=args.app.parent/'sync-state.json'
    dictionary=json.loads((args.app/'docs/data_dictionary.json').read_text(encoding='utf-8'))
    child=None;active=None;active_manifest=None;last_pointer=None
    def shutdown(*_):
        stop(child);raise SystemExit(0)
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    while True:
        try:
            pointer=(args.releases/'latest.json').read_bytes()
            if pointer!=last_pointer:
                directory,manifest=validate_release(args.releases,dictionary)
                probe=run_api(args.app,directory,18081)
                try:
                    if not healthy(probe,18081,manifest):raise RuntimeError('Candidate API health check failed')
                finally:stop(probe)
                previous=active
                stop(child)
                child=run_api(args.app,directory,18080)
                if not healthy(child,18080,manifest):
                    stop(child)
                    child=run_api(args.app,active,18080) if active else None
                    raise RuntimeError('Activation failed; previous release restored')
                active=directory;active_manifest=manifest;last_pointer=pointer
                tmp=state.with_suffix('.tmp')
                tmp.write_text(json.dumps({'active':str(active),'previous':str(previous) if previous else None,'sha256':manifest['sha256'],'rows':manifest['rows'],'months':len(manifest['months']),'synced_at':time.strftime('%Y-%m-%dT%H:%M:%S%z')},ensure_ascii=False,indent=2),encoding='utf-8')
                tmp.replace(state)
                print('Published '+str(active),flush=True)
            if child is not None and child.poll() is not None:
                child=run_api(args.app,active,18080)
                if not healthy(child,18080,active_manifest):raise RuntimeError('API recovery failed')
        except Exception as error:
            print('Sync failed; retaining last working release: '+str(error),flush=True)
        # Keep checking child exits while waiting for the next pointer check.
        for _ in range(60):
            time.sleep(10)
            if child is not None and child.poll() is not None:break


if __name__=='__main__':main()
