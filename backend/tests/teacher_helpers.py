"""教师端测试辅助函数。"""


def login_teacher(client, monkeypatch, password='test-teacher-password'):
    """通过 HTTP 登录获取教师令牌，避免直接操作 TEACHER_TOKENS。

    Args:
        client: FastAPI TestClient 实例
        monkeypatch: pytest monkeypatch fixture
        password: 教师密码，默认 'test-teacher-password'

    Returns:
        str: 教师令牌
    """
    monkeypatch.setenv('TEACHER_PASSWORD', password)
    response = client.post('/api/teacher/login', json={'password': password})
    assert response.status_code == 200, response.text
    return response.json()['teacher_token']
