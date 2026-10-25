"""Ensure documented module entrypoints survive directory reorganization."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('module',[
    'tools.data.build_database', 'tools.data.build_regional_dataset',
    'tools.model.train_random_forest', 'tools.model.evaluate_random_forest',
    'tools.release.package_code', 'tools.release.check_deployment',
])
def test_documented_module_help(module):
    result=subprocess.run([sys.executable,'-m',module,'--help'],cwd=ROOT,
                          capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode('utf-8','replace')
    assert b'usage:' in result.stdout.lower()


def test_data_plan_is_available_without_data_or_downloads():
    result=subprocess.run([sys.executable,'-m','tools.data.build_regional_dataset',
                           '--config','config/east_asia_2012_2024.json','--plan'],cwd=ROOT,
                          capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode('utf-8','replace')
    plan=json.loads(result.stdout)
    assert len(plan['months'])==156 and plan['rows']==6240000
