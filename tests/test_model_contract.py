import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
from backend.app import prediction
from models.features import build_feature_list, assign_status
from models.random_forest import RFFishing


@pytest.mark.parametrize('columns',[prediction.FEATURE_ORDER,prediction.FEATURE_ORDER[:4]])
def test_saved_training_regressor_matches_api_once(tmp_path,monkeypatch,columns):
    m_sin,m_cos=prediction.month_to_cyclic(2)
    values=[30,120,m_sin,m_cos,10,1,2,0,.1]
    full=pd.DataFrame([values,[v+.1 for v in values], [v+.2 for v in values]],columns=prediction.FEATURE_ORDER)
    features=full[columns]
    model=RFFishing(n_estimators=4,max_depth=2,min_samples_leaf=1)
    model.fit(features,[0,3,8])
    model.save(tmp_path/'model.joblib')
    monkeypatch.setenv('FISHERIES_MODEL_DIR',str(tmp_path))
    prediction.get_model.cache_clear()
    try:
        result=prediction.predict_fishing_hours(30,120,2,10,1,2,0,.1)
        assert result==pytest.approx(model.predict(features.iloc[:1])[0])
    finally:prediction.get_model.cache_clear()


def test_missing_target_is_not_a_reported_zero():
    frame=pd.DataFrame({'fishing_hours':[np.nan,0,2],'hours':[np.nan,0,4]})
    assert assign_status(frame).tolist()==['no_record','reported_zero','observed']
    assert not set(build_feature_list(frame)) & {'hours','fishing_hours','source_row_count'}


def test_training_cli_accepts_official_date_csv(tmp_path):
    source=tmp_path/'csv';source.mkdir()
    pd.DataFrame([{'date':f'2012-{month:02d}-01','cell_ll_lat':30+i*.1,'cell_ll_lon':120+i*.1,
                   'fishing_hours':i+month,'hours':i+month+2}
                  for month in range(1,4) for i in range(6)]).to_csv(source/'fixture.csv',index=False)
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-m','tools.model.train_random_forest','--source','csv',
                           '--csv_dir',str(source),'--split_mode','none','--n_estimators','4',
                           '--min_samples_leaf','1','--max_depth','3'],cwd=root,
                          env={**os.environ,'FISHERIES_MODEL_RUNS_DIR':str(tmp_path/'runs'),'PYTHONIOENCODING':'utf-8'},
                          capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode==0,result.stderr
    assert len(list((tmp_path/'runs').glob('*/model.joblib')))==1
