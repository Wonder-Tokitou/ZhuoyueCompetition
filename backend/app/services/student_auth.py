"""Persistent, revocable student logins; case links are not identity credentials."""
import hashlib
import hmac
import secrets
from datetime import datetime

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.db import get_db

bearer = HTTPBearer(auto_error=False)
ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_student(credentials: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
    if credentials is None:
        raise HTTPException(401, "请先登录学生账号")
    login = db.get(models.StudentLogin, token_hash(credentials.credentials))
    if login is None or login.expires_at <= datetime.now():
        raise HTTPException(401, "学生登录已失效，请重新登录")
    student = db.get(models.StudentAccount, login.student_id)
    if student is None or student.status != "active":
        raise HTTPException(403, "学生账号已停用，请联系教师")
    return student
