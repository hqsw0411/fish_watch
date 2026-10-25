import tarfile
import pytest
from scripts.package_code import REQUIRED,collect_files,package


def fixture_tree(root):
    for name in REQUIRED | {'models/features.py','frontend/src/extra.jsx','data/raw/source.nc',
                            'data/sample.sqlite3','models/runs/a/model.joblib','.env',
                            'frontend/node_modules/vendor.js'}:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')


def test_code_package_keeps_tsx_map_and_model_source_not_weights(tmp_path):
    root=tmp_path/'project';fixture_tree(root)
    target=tmp_path/'code.tar.gz';package(root,target)
    with tarfile.open(target) as archive:names={m.name.removeprefix('fisheries-server/') for m in archive}
    assert REQUIRED <= names
    assert 'frontend/src/extra.jsx' in names
    assert not any(name.startswith(('data/','models/runs/')) or 'node_modules' in name or name=='.env' for name in names)
    with pytest.raises(FileExistsError):package(root,target)


def test_missing_frontend_entry_blocks_package(tmp_path):
    fixture_tree(tmp_path)
    (tmp_path/'frontend/src/main.tsx').unlink()
    with pytest.raises(FileNotFoundError,match='main.tsx'):collect_files(tmp_path)
