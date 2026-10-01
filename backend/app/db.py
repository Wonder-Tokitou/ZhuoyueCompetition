"""数据库连接层：engine / SessionLocal / get_db。

初赛用 SQLite 单文件零运维，库文件固定在 backend/data/app.db。
建表用 SQLAlchemy 的 create_all()，不引入 alembic。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# backend/app/db.py -> 上溯两级即 backend/
BACKEND_DIR = Path(__file__).resolve().parents[1]
# 让迁移、seed、测试脚本与 uvicorn 使用同一份本地配置。
# main.py 也会加载一次 .env；load_dotenv 默认不会覆盖调用方显式设置的环境变量。
load_dotenv(BACKEND_DIR / ".env")
DATA_DIR = BACKEND_DIR / "data"
# 必须先把目录建出来，否则 SQLite 首次建库会直接失败
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///%s" % DB_PATH.as_posix()

# check_same_thread=False：FastAPI 的依赖注入会跨线程复用连接
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """所有模型的基类。"""


def get_db():
    """FastAPI 依赖：每个请求一个会话，请求结束必定关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
