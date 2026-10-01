from backend.app.integrations.ai import tutor as tutor_adapter
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import models
from backend.app.main import app
from backend.app.routers import teacher
from backend.app.integrations.ai import artifacts
from ai_component.agents import tutor_agent
from backend.app.services.student_auth import hash_password
from backend.app.services.student_auth import verify_password
from backend.tests.account_helpers import login_student
from backend.tests.teacher_helpers import login_teacher
from backend.tests.test_student_workflow import env
from backend.tests.test_student_workflow import begin
from backend.tests.test_student_workflow import complete
from backend.tests.test_student_workflow import URL


def test_password_storage_and_login_revocation(env):
    client, factory = env
    a, b = hash_password('a-long-password'), hash_password('a-long-password')
    assert a != b and verify_password('a-long-password', a)
    assert not verify_password('wrong', a)
    old = client.headers['Authorization']
    assert client.post('/api/student/logout').status_code == 200
    assert client.get('/api/student/me').status_code == 401
    fresh = client.post('/api/student/login', json={'username': 'student', 'password': 'test-password'}).json()
    assert old != 'Bearer ' + fresh['access_token']
    client.headers['Authorization'] = 'Bearer ' + fresh['access_token']
    assert client.post('/api/student/password', json={'current_password': 'test-password', 'new_password': 'new-password'}).status_code == 200
    assert client.get('/api/student/me').status_code == 401
    assert client.post('/api/student/login', json={'username': 'student', 'password': 'test-password'}).status_code == 401
    assert client.post('/api/student/login', json={'username': 'student', 'password': 'new-password'}).status_code == 200
    with factory() as db:
        row = db.scalar(select(models.StudentAccount))
        assert 'new-password' not in row.password_hash
        assert all(len(row.token_hash) == 64 for row in db.scalars(select(models.StudentLogin)))


def test_student_self_register_save_and_teacher_read_end_to_end(env, monkeypatch):
    client, factory = env
    # Student can register without any existing student or teacher login.
    client.headers.pop('Authorization', None)
    response = client.post('/api/student/register', json={
        'username': 'classmate-2026', 'real_name': '王同学', 'password': 'student-pass'})
    assert response.status_code == 200, response.text
    identity = response.json()
    client.headers['Authorization'] = 'Bearer ' + identity['access_token']
    assert client.get('/api/student/me').json()['real_name'] == '王同学'
    state = client.post(URL + '/sessions', json={'student_name': '王同学'}).json()
    sid = complete(client, state)
    assert client.get('/api/student/records').json()[0]['session_id'] == sid
    token = login_teacher(client, monkeypatch)
    accounts = client.get('/api/teacher/students', params={'token': token}).json()
    assert any(row['real_name'] == '王同学' and row['username'] == 'classmate-2026' for row in accounts)
    records = client.get('/api/cases/1/records', params={'token': token}).json()
    record = next(row for row in records if row['session_id'] == sid)
    assert record['student_name'] == '王同学'
    assert record['student_id'] == identity['student']['id']
    assert len(record['turns']) == 3


def test_cross_student_access_denied_even_with_same_real_name(env):
    alice, factory = env
    state = begin(alice)
    sid = state['session_id']
    with TestClient(app) as bob:
        login_student(bob, factory, username='another', real_name='student')
        assert bob.get('/api/student/records').json() == []
        for suffix in [f'/sessions/{sid}', f'/sessions/{sid}/messages', f'/review?session_id={sid}']:
            assert bob.get(URL + suffix).status_code == 404
        assert bob.post(URL + '/review/retry', params={'session_id': sid}).status_code == 404
        assert bob.post(URL + '/chat', json={'session_id': sid, 'message': '市场份额？'}).status_code == 404
        assert bob.post(URL + '/decide', json={'session_id': sid, 'student_name': 'student', 'node_id': state['play']['nodes'][0]['id'], 'option_key': 'A', 'duration_ms': 1}).status_code == 404
        own = bob.post(URL + '/sessions', json={'student_name': '冒充姓名'}).json()
        assert own['student_name'] == 'student'
        with factory() as db:
            row = db.get(models.Session, own['session_id'])
            assert row.attempt_no == 1
    with TestClient(app) as anonymous:
        assert anonymous.get(URL).status_code == 401
        assert anonymous.get('/api/student/cases').status_code == 401


def test_teacher_management_disable_reset_and_student_cannot_manage(env, monkeypatch):
    client, factory = env
    assert client.get('/api/teacher/students', params={'token': 'fake'}).status_code == 403
    token = login_teacher(client, monkeypatch)
    params = {'token': token}
    payload = {'username': 'NEW_STUDENT', 'real_name': '李同学', 'password': 'initial-password'}
    row = client.post('/api/teacher/students', params=params, json=payload).json()
    assert row['username'] == 'new_student' and 'password_hash' not in row
    assert client.post('/api/teacher/students', params=params, json=payload).status_code == 409
    login = client.post('/api/student/login', json={'username': 'new_student', 'password': 'initial-password'}).json()
    client.headers['Authorization'] = 'Bearer ' + login['access_token']
    assert client.patch('/api/teacher/students/' + str(row['id']), params=params, json={'status': 'disabled'}).status_code == 200
    assert client.get('/api/student/me').status_code in (401, 403)
    assert client.post('/api/student/login', json={'username': 'new_student', 'password': 'initial-password'}).status_code == 401
    client.patch('/api/teacher/students/' + str(row['id']), params=params, json={'status': 'active', 'password': 'reset-password'})
    assert client.post('/api/student/login', json={'username': 'new_student', 'password': 'reset-password'}).status_code == 200


def test_expiry_and_failed_login_limit(env):
    client, factory = env
    with factory() as db:
        for row in db.scalars(select(models.StudentLogin)):
            row.expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
    assert client.get('/api/student/me').status_code == 401
    for _ in range(5):
        assert client.post('/api/student/login', json={'username': 'student', 'password': 'bad'}).status_code == 401
    assert client.post('/api/student/login', json={'username': 'student', 'password': 'test-password'}).status_code == 429


def test_library_own_records_and_submission_hierarchy(env):
    client, factory = env
    listed = client.get('/api/student/cases').json()
    assert len(listed) == 1 and 'source_text' not in listed[0] and 'teacher_token' not in listed[0]
    sid = complete(client, begin(client))
    saved = client.get('/api/student/records').json()
    assert len(saved) == 1 and saved[0]['session_id'] == sid and len(saved[0]['turns']) == 3
    with factory() as db:
        row = db.scalar(select(models.AiArtifact).where(models.AiArtifact.kind == 'student_submission').order_by(models.AiArtifact.id.desc()))
        assert '/cases/1/students/1/submissions/1/' in row.path.replace('\\', '/')
        content = artifacts.artifact_store.read_json(row)
        assert content['state']['finished'] and len(content['state']['turns']) == 3
        db.get(models.Case, 1).status = 'draft'
        db.commit()
    assert client.get('/api/student/cases').json() == []
    assert client.get('/api/student/records').json()[0]['available'] is False


def test_start_session_rejects_a_second_record_for_same_student_and_case(env):
    client, _ = env
    begin(client)
    response = client.post(URL + '/sessions', json={'student_name': 'student'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'submission_exists'


def test_student_records_list_is_read_only(env):
    client, factory = env
    sid = complete(client, begin(client))
    listed = client.get('/api/student/records').json()
    assert len(listed) == 1 and listed[0]['session_id'] == sid
    with factory() as db:
        assert db.get(models.Session, sid) is not None
    # Legacy duplicates, when present in an older database, must remain visible
    # here so owners can delete them; the endpoint has no cleanup side effects.


def test_completed_run_can_be_rolled_back_while_review_is_queued(env):
    client, factory = env
    sid = complete(client, begin(client))
    with factory() as db:
        task = db.scalar(select(models.AiTask).where(models.AiTask.session_id == sid))
        assert task is not None and task.status == 'queued'
        task_id = task.task_id
        db.commit()
    response = client.post(URL + f'/sessions/{sid}/rollback/2')
    assert response.status_code == 200, response.text
    assert len(response.json()['turns']) == 1
    with factory() as db:
        assert db.scalar(select(models.AiTask).where(models.AiTask.task_id == task_id)) is None


def test_unrelated_tutor_question_never_reads_files_or_searches(env, monkeypatch):
    client, _ = env
    sid = begin(client)['session_id']
    model = AsyncMock(return_value={'business': False, 'tools': [], 'search_query': ''})
    monkeypatch.setattr(tutor_agent, 'chat_json', model)
    search = AsyncMock(side_effect=AssertionError('must not search'))
    monkeypatch.setattr(tutor_adapter, 'search_business', search)
    response = client.post(URL + '/chat', json={'session_id': sid, 'message': '写一首爱情诗'})
    assert '暂不作答' in response.text and model.await_count == 1
    search.assert_not_called()
    assert model.call_args.kwargs['reasoning_effort'] == 'low'


def test_tutor_web_sources_separated_and_query_bounded(env, monkeypatch):
    client, _ = env
    sid = begin(client)['session_id']
    query = '现金流经营分析'
    model = AsyncMock(side_effect=[{'business': True, 'tools': ['read_results'], 'search_query': query},
        {'answer': '案例数据见[read_case]，概念补充见[web_1]。', 'source_ids': ['read_case', 'web_1']}])
    monkeypatch.setattr(tutor_agent, 'chat_json', model)
    search = AsyncMock(return_value=([{'id': 'web_1', 'title': '公开资料', 'url': 'https://worldbank.org/example', 'retrieved_at': '2026-09-25', 'content': '公开商科定义'}], ''))
    monkeypatch.setattr(tutor_adapter, 'search_business', search)
    response = client.post(URL + '/chat', json={'session_id': sid, 'message': '现金流含义'})
    assert '外部补充' in response.text and 'worldbank.org' in response.text
    search.assert_awaited_once_with(query)


def test_legacy_anonymous_records_are_not_claimed_by_matching_name(env):
    client, factory = env
    state = begin(client)
    with factory() as db:
        db.get(models.Session, state['session_id']).student_id = None
        db.commit()
    assert client.get('/api/student/records').json() == []
    assert client.get(URL + '/sessions/' + str(state['session_id'])).status_code == 404


def test_sensitive_validation_and_static_paths_not_exposed(env):
    client, _ = env
    response = client.post('/api/student/login', json={'username': 'student', 'password': {'private-password-value': 'secret'}})
    assert response.status_code == 422 and 'private-password-value' not in response.text
    for path in ['/..%2f..%2fbackend%2f.env', '/%2e%2e/%2e%2e/backend/.env']:
        assert client.get(path).status_code == 404


def test_recovery_picks_only_persisted_pending_tasks(env, monkeypatch):
    import asyncio
    from backend.app.integrations.ai import task_runner
    _, factory = env
    with factory() as db:
        for status in ['queued', 'running', 'succeeded', 'failed', 'cancelled']:
            db.add(models.AiTask(task_id='recovery-' + status, kind='student_review', status=status))
        db.commit()
    scheduled = []
    monkeypatch.setattr(task_runner, 'schedule_task', scheduled.append)
    asyncio.run(task_runner.recover_pending_tasks())
    assert set(scheduled) == {'recovery-queued', 'recovery-running'}


def test_teacher_token_survives_process_memory_loss(env, monkeypatch):
    """回归：模拟「登录在 worker A、请求被路由到 worker B」。

    清空进程内缓存后，令牌必须仍能通过鉴权 —— 否则就是线上「教师端登录后
    约两秒被踢回登录页」的复现（403 → 前端清 sessionStorage → 跳 /teacher/login）。
    """
    client, factory = env
    monkeypatch.setenv('TEACHER_PASSWORD', 'token-persistence-secret')
    login = client.post('/api/teacher/login', json={'password': 'token-persistence-secret'})
    assert login.status_code == 200, login.text
    token = login.json()['teacher_token']

    # 落库：库中确实存在该令牌
    with factory() as db:
        assert db.get(models.TeacherSession, token) is not None

    # 关键：把进程内缓存换成空集合，模拟"另一个 worker 从未见过这次登录"。
    # 用 monkeypatch 替换而非就地 discard/Clear —— 测试结束后自动还原，不污染全局。
    # 这是本用例唯一的进程内状态操作，且是**语义必需**：不清空缓存就无法证明
    # 「缓存未命中时仍能凭数据库放行」，也就测不出线上那个 403 缺陷。
    monkeypatch.setattr(teacher, 'TEACHER_TOKENS', set())

    # 仍应放行（命中数据库），而不是 403
    assert client.get('/api/teacher/students', params={'token': token}).status_code == 200
    assert client.get('/api/cases', params={'token': token}).status_code == 200

    # 反例：既不在缓存也不在库里的令牌必须 403
    assert client.get('/api/teacher/students', params={'token': 'not-issued-anywhere'}).status_code == 403


def test_teacher_token_expiry_revokes_access(env, monkeypatch):
    """回归：过期令牌必须被拒，且登录时会顺手清理过期行。"""
    client, factory = env
    monkeypatch.setenv('TEACHER_PASSWORD', 'expiry-secret')
    token = client.post('/api/teacher/login', json={'password': 'expiry-secret'}).json()['teacher_token']
    with factory() as db:
        row = db.get(models.TeacherSession, token)
        row.expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
    # 同上：空缓存 + 库中过期行 → 必须 403，证明过期判定走的是数据库
    monkeypatch.setattr(teacher, 'TEACHER_TOKENS', set())
    assert client.get('/api/teacher/students', params={'token': token}).status_code == 403
    # 再次登录会清掉过期行
    client.post('/api/teacher/login', json={'password': 'expiry-secret'})
    with factory() as db:
        assert db.get(models.TeacherSession, token) is None


def test_teacher_logout_revokes_token(env, monkeypatch):
    """回归：登出后令牌立即失效（缓存与数据库双清），再次请求 403。"""
    client, factory = env
    monkeypatch.setenv('TEACHER_PASSWORD', 'logout-secret')
    token = client.post('/api/teacher/login', json={'password': 'logout-secret'}).json()['teacher_token']
    assert client.get('/api/cases', params={'token': token}).status_code == 200

    assert client.post('/api/teacher/logout', params={'token': token}).status_code == 200

    with factory() as db:
        assert db.get(models.TeacherSession, token) is None
    assert token not in teacher.TEACHER_TOKENS
    # 清空缓存后再试，确保不是缓存之外的假象
    monkeypatch.setattr(teacher, 'TEACHER_TOKENS', set())
    assert client.get('/api/cases', params={'token': token}).status_code == 403

    # 幂等：重复登出不报错
    assert client.post('/api/teacher/logout', params={'token': token}).status_code == 200
