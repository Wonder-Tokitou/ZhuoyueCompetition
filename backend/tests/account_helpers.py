from backend.app import models
from backend.app.services.student_auth import hash_password


def login_student(client, factory, username="student", real_name="student"):
    with factory() as db:
        row = models.StudentAccount(username=username, real_name=real_name, password_hash=hash_password("test-password"))
        db.add(row)
        db.commit()
        student_id = row.id
    response = client.post('/api/student/login', json={"username": username, "password": "test-password"})
    assert response.status_code == 200, response.text
    client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
    return student_id
