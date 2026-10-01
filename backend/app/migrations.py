"""幂等 SQLite 增量迁移。只追加列，不删除或重建业务数据。"""
import shutil
from pathlib import Path
from sqlalchemy import inspect, text
from backend.app.observability import event
from backend.app.db import engine

def migrate() -> None:
    # Back up the database actually being migrated, never the default production
    # path when a test or deployment supplied DATABASE_URL.
    if engine.dialect.name == "sqlite" and engine.url.database not in (None, "", ":memory:"):
        db_path = Path(engine.url.database).resolve()
        backup = Path(str(db_path) + ".bak-v5")
        if db_path.exists() and not backup.exists():
            shutil.copy2(db_path, backup)
    insp = inspect(engine)
    additions = {
        "case": {"version": "INTEGER NOT NULL DEFAULT 1", "input_filename": "VARCHAR(255)", "input_mime": "VARCHAR(128)", "input_size": "INTEGER", "base_net_profit": "FLOAT", "financial_assumptions_json": "JSON"},
        "option_result": {"net_profit": "FLOAT", "financial_basis_json": "JSON"},
        "session": {"student_id": "INTEGER REFERENCES student_account(id)", "case_version": "INTEGER NOT NULL DEFAULT 1", "case_snapshot_json": "JSON NOT NULL DEFAULT '{}'", "current_metrics_json": "JSON NOT NULL DEFAULT '{}'"},
        "turn": {"before_metrics_json": "JSON", "after_metrics_json": "JSON", "delta_metrics_json": "JSON", "result_source": "VARCHAR(32)"},
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in inspect(conn).get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (table, name, ddl)))
        if insp.has_table("case"):
            conn.execute(text('UPDATE "case" SET version=1 WHERE version IS NULL'))
        if insp.has_table("session"):
            conn.execute(text('UPDATE "session" SET case_version=1 WHERE case_version IS NULL'))
            conn.execute(text("UPDATE \"session\" SET case_snapshot_json='{}' WHERE case_snapshot_json IS NULL"))
            conn.execute(text("UPDATE \"session\" SET current_metrics_json='{}' WHERE current_metrics_json IS NULL"))
            # Keep legacy submissions intact. Add a DB-level one-run constraint
            # only when existing logged-in records are already unambiguous.
            duplicates = conn.execute(text(
                'SELECT COUNT(*) FROM (SELECT case_id, student_id FROM "session" '
                'WHERE student_id IS NOT NULL GROUP BY case_id, student_id HAVING COUNT(*) > 1)'
            )).scalar_one()
            if not duplicates:
                conn.execute(text(
                    'CREATE UNIQUE INDEX IF NOT EXISTS uq_session_case_student '
                    'ON "session" (case_id, student_id) WHERE student_id IS NOT NULL'
                ))
            else:
                event("未创建学生案例唯一索引：保留了历史重复提交，需由学生或教师确认清理",
                      level=30, duplicate_groups=int(duplicates))
