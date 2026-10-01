import pytest
from backend.app import models
from backend.tests.test_student_workflow import env, begin, URL


@pytest.mark.parametrize('text,expected',[
    ('约22%（教学假设，参照制造类行业20%—35%区间）',200),
    ('22％',200),('20%—35%',422),('未知',422)])
def test_existing_session_percentage_text(env,text,expected):
    client,factory=env
    state=begin(client)
    with factory() as db:
        session=db.get(models.Session,state['session_id'])
        session.current_metrics_json={**session.current_metrics_json,'gross_margin':text}
        db.commit()
    node=state['play']['nodes'][0]
    response=client.post(URL+'/decide',json={'session_id':state['session_id'],
        'node_id':node['id'],'option_key':node['options'][0]['key'], 'student_name':'student','duration_ms':10})
    assert response.status_code==expected,response.text
    if expected==200:
        assert response.json()['result']['before_metrics']['gross_margin']=='22.0%'
    else:
        with factory() as db:
            assert db.query(models.Turn).filter_by(session_id=state['session_id']).count()==0
