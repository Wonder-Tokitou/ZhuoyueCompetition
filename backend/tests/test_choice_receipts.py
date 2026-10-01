"""Choice identity must survive shuffle, persistence, teacher edits and reporting."""
import pytest
from sqlalchemy import select
from backend.app import models
from backend.tests.test_student_workflow import env, begin, URL
from backend.tests.teacher_helpers import login_teacher


@pytest.mark.parametrize('display_index', [0, 1, 2])
def test_every_displayed_choice_returns_frozen_content_and_exact_result(env, monkeypatch, display_index):
    client, factory = env
    state = begin(client)
    sid = state['session_id']
    for node in state['play']['nodes']:
        chosen = node['options'][display_index]
        with factory() as db:
            session = db.get(models.Session, sid)
            option = next(o for n in session.case_snapshot_json['nodes'] if n['id'] == node['id']
                          for o in n['options'] if o['key'] == chosen['key'])
            expected = option['metrics']
            live = db.scalar(select(models.OptionResult).where(models.OptionResult.node_id == node['id'],
                                models.OptionResult.option_key == chosen['key']))
            live.label = '老师后来修改的选项，不属于本次冻结题面'
            db.commit()
        payload = {'session_id': sid, 'student_name': 'student', 'node_id': node['id'],
                   'option_key': chosen['key'], 'duration_ms': 20}
        response = client.post(URL + '/decide', json=payload)
        assert response.status_code == 200, response.text
        assert response.json()['result']['after_metrics'] == expected
        assert client.post(URL + '/decide', json=payload).status_code == 409
        saved = client.get(URL + f'/sessions/{sid}').json()
        turn = saved['turns'][-1]
        assert turn['chosen_option'] == chosen['key']
        assert turn.get('display_option') == 'ABC'[display_index]
        assert turn.get('chosen_label') == chosen['label']
        assert saved['play']['nodes'][node['idx']-1]['options'] == node['options']
    assert len(saved['turns']) == 3
    assert saved['finished']
    student_records = client.get('/api/student/records').json()[0]
    token = login_teacher(client, monkeypatch)
    records = client.get('/api/cases/1/records', params={'token':token}).json()
    for a, b, c in zip(saved['turns'], student_records['turns'], records[0]['turns']):
        for field in ('chosen_option', 'display_option', 'chosen_label'):
            assert a[field] == b[field] == c[field]
