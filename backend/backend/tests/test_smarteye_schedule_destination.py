"""Resume ownership is checked without starting a scheduler or contacting an upstream."""
import pytest
from flask import Flask, jsonify
from sqlalchemy import create_engine, insert
from integrations.smarteye import hooks, runtime


@pytest.fixture
def resume(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///:memory:')
    runtime.metadata.create_all(engine)
    monkeypatch.setattr(runtime,'_runtime',runtime.Runtime(engine,tmp_path))
    monkeypatch.setattr(hooks,'_installed',False)
    monkeypatch.setattr(hooks,'persist_schedulers',lambda:None)
    app=Flask(__name__)
    @app.post('/ledger-image/scheduler/start')
    def inspect_owner():return jsonify(code=0,data=dict(runtime.actor.get()))
    hooks.install_hooks(app)
    def request(saved_owner,table_tenant=1):
        with engine.begin() as conn:
            conn.execute(insert(runtime.schedules).values(sched_id='old-plan',tenant_id=table_tenant,payload={'owner':saved_owner}))
        token=runtime.actor.set({'tenant_id':1,'user_id':7,'role':'admin','project_id':'new-project',
                                'collection_id':'new-collection','min_long_edge':1080})
        try:return app.test_client().post('/ledger-image/scheduler/start',json={'sched_id':'old-plan'})
        finally:runtime.actor.reset(token)
    yield request
    engine.dispose()


def test_download_only_plan_drops_current_destination(resume):
    response=resume({'tenant_id':1,'user_id':7})
    assert response.status_code==200
    assert all(key not in response.json['data'] for key in ('project_id','collection_id','min_long_edge'))
    assert response.json['data']['tenant_id']==1 and response.json['data']['role']=='admin'


def test_saved_scope_overrides_selection_without_losing_current_auth_context(resume):
    response=resume({'project_id':'saved-project','collection_id':'saved-collection','min_long_edge':0})
    assert response.status_code==200
    owner=response.json['data']
    assert (owner['project_id'],owner['collection_id'],owner['min_long_edge'])==('saved-project','saved-collection',0)
    assert (owner['tenant_id'],owner['user_id'],owner['role'])==(1,7,'admin')


@pytest.mark.parametrize('saved_owner,table_tenant',[({'tenant_id':2,'project_id':'foreign-project'},1),({'tenant_id':1},2)])
def test_resume_rejects_cross_tenant_saved_owner_or_plan(resume,saved_owner,table_tenant):
    response=resume(saved_owner,table_tenant)
    assert response.status_code in (403,404)
