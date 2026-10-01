"""Student personal space and teacher-managed real-name accounts."""
import secrets
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.db import get_db
from backend.app.services.student_auth import bearer
from backend.app.services.student_auth import current_student
from backend.app.services.student_auth import hash_password
from backend.app.services.student_auth import token_hash
from backend.app.services.student_auth import verify_password
from backend.app.routers.teacher import is_valid_teacher_token
from backend.app.domain.student_choices import choice_receipt

router = APIRouter(prefix="/api", tags=["学生账号与管理"])


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class CreateInput(LoginInput):
    real_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class ManageInput(BaseModel):
    real_name: str | None = Field(default=None, min_length=1, max_length=64)
    status: Literal["active", "disabled"] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)


class PasswordInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def view(row):
    return {"id": row.id, "username": row.username, "real_name": row.real_name,
            "status": row.status, "created_at": row.created_at}


def require_teacher(token: str = Query(...), db: Session = Depends(get_db)):
    # 令牌以数据库为准：多 worker 下任一进程都能校验通过，不再出现「登录即被踢出」。
    if not is_valid_teacher_token(db, token):
        raise HTTPException(403, "教师登录已失效")


@router.post("/student/register")
def register(payload: CreateInput, db: Session = Depends(get_db)):
    """Local teaching pilot: students fill in their own name; no teacher provisioning needed."""
    create_student(payload, db)
    return login(LoginInput(username=payload.username, password=payload.password), db)


@router.post("/student/login")
def login(payload: LoginInput, db: Session = Depends(get_db)):
    row = db.scalar(select(models.StudentAccount).where(models.StudentAccount.username == payload.username.lower()))
    now = datetime.now()
    if row and row.locked_until and row.locked_until > now:
        raise HTTPException(429, "登录失败次数过多，请十五分钟后重试")
    # Also perform a password derivation for unknown accounts.
    valid = verify_password(payload.password, row.password_hash if row else
                            "pbkdf2_sha256$600000$unknown-account$" + "0" * 64)
    if not row or not valid or row.status != "active":
        if row:
            row.failed_logins += 1
            if row.failed_logins >= 5:
                row.locked_until = now + timedelta(minutes=15)
                row.failed_logins = 0
            db.commit()
        raise HTTPException(401, "账号或密码错误，或账号已停用")
    row.failed_logins = 0
    row.locked_until = None
    token = secrets.token_urlsafe(32)
    db.execute(delete(models.StudentLogin).where(models.StudentLogin.expires_at <= now))
    db.add(models.StudentLogin(token_hash=token_hash(token), student_id=row.id, expires_at=now + timedelta(hours=12)))
    db.commit()
    return {"access_token": token, "student": view(row)}


@router.get("/student/me")
def me(student=Depends(current_student)):
    return view(student)


@router.post("/student/logout")
def logout(student=Depends(current_student), credentials=Depends(bearer), db: Session = Depends(get_db)):
    db.execute(delete(models.StudentLogin).where(models.StudentLogin.token_hash == token_hash(credentials.credentials)))
    db.commit()
    return {"ok": True}


@router.post("/student/password")
def change_password(payload: PasswordInput, student=Depends(current_student), db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, student.password_hash):
        raise HTTPException(400, "当前密码错误")
    student.password_hash = hash_password(payload.new_password)
    db.execute(delete(models.StudentLogin).where(models.StudentLogin.student_id == student.id))
    db.commit()
    return {"ok": True}


@router.get("/student/cases")
def cases(student=Depends(current_student), db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Case).where(models.Case.status == "published", models.Case.owner_type == "teacher")
                      .order_by(models.Case.published_at.desc(), models.Case.id.desc())).all()
    return [{"id": r.id, "title": r.title, "case_type": r.case_type, "student_token": r.student_token,
             "version": r.version, "background": r.background} for r in rows]


@router.get("/student/records")
def records(student=Depends(current_student), db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Session).where(models.Session.student_id == student.id)
                      .order_by(models.Session.created_at.desc(), models.Session.id.desc())).all()
    # Listing is read-only. Never delete historical submissions as a side effect
    # of refreshing this page. Return all legacy rows so students can explicitly
    # delete duplicates rather than hiding them while the teacher still sees them.
    result = []
    for r in rows:
        review = next((v for v in r.reviews if v.attempt_no == r.attempt_no), None)
        result.append({"session_id": r.id, "case_id": r.case_id,
            "case_title": r.case_snapshot_json.get("title", r.case.title), "student_token": r.case.student_token,
            "case_version": r.case_version, "attempt_no": r.attempt_no, "created_at": r.created_at,
            "finished_at": r.finished_at, "turn_count": len(r.turns), "available": r.case.status == "published",
            "turns": [{"node_id": t.node_id, "chosen_option": t.chosen_option, "input_text": t.input_text,
                       **choice_receipt(r, t.node_id, t.chosen_option),
                       "result": t.result_json} for t in r.turns],
            "review": {"dimensions": review.dimensions_json, "conclusion": review.conclusion} if review else None})
    return result


@router.get("/teacher/students", dependencies=[Depends(require_teacher)])
def students(db: Session = Depends(get_db)):
    return [view(row) for row in db.scalars(select(models.StudentAccount).order_by(models.StudentAccount.id.desc()))]


@router.post("/teacher/students", dependencies=[Depends(require_teacher)])
def create_student(payload: CreateInput, db: Session = Depends(get_db)):
    if not payload.real_name.strip():
        raise HTTPException(400, "真实姓名不能为空")
    row = models.StudentAccount(username=payload.username.lower(), real_name=payload.real_name.strip(),
                                password_hash=hash_password(payload.password))
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "学生账号已存在")
    return view(row)


@router.patch("/teacher/students/{student_id}", dependencies=[Depends(require_teacher)])
def manage_student(student_id: int, payload: ManageInput, db: Session = Depends(get_db)):
    row = db.get(models.StudentAccount, student_id)
    if row is None:
        raise HTTPException(404, "学生账号不存在")
    if payload.real_name is not None:
        if not payload.real_name.strip():
            raise HTTPException(400, "真实姓名不能为空")
        row.real_name = payload.real_name.strip()
    if payload.status is not None:
        row.status = payload.status
    if payload.password is not None:
        row.password_hash = hash_password(payload.password)
        row.failed_logins = 0
        row.locked_until = None
    if payload.password or payload.status == "disabled":
        db.execute(delete(models.StudentLogin).where(models.StudentLogin.student_id == row.id))
    db.commit()
    return view(row)
