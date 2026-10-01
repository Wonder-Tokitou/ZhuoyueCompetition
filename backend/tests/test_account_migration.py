from sqlalchemy import create_engine, inspect, text

from backend.app import migrations
from backend.app import models


def test_legacy_records_preserved_and_not_claimed_by_name(tmp_path, monkeypatch):
    path = tmp_path / 'old.db'
    engine = create_engine('sqlite:///' + path.as_posix())
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE "case" (id INTEGER PRIMARY KEY, title TEXT)'))
        conn.execute(text('CREATE TABLE "session" (id INTEGER PRIMARY KEY, case_id INTEGER, student_name TEXT)'))
        conn.execute(text('INSERT INTO "case" VALUES (7, :title)'), {'title': '原案例'})
        conn.execute(text('INSERT INTO "session" VALUES (9, 7, :name)'), {'name': '同名学生'})
    monkeypatch.setattr(migrations, 'engine', engine)
    migrations.migrate()
    migrations.migrate()
    assert (tmp_path / 'old.db.bak-v5').exists()
    assert 'student_id' in {c['name'] for c in inspect(engine).get_columns('session')}
    with engine.connect() as conn:
        row = conn.execute(text('SELECT id, student_name, student_id, case_version FROM "session"')).one()
        assert tuple(row) == (9, '同名学生', None, 1)
        assert conn.execute(text('SELECT title FROM "case"')).scalar() == '原案例'
    engine.dispose()


def test_migration_before_seed_on_empty_database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'new.db').as_posix())
    monkeypatch.setattr(migrations, 'engine', engine)
    migrations.migrate()
    models.Base.metadata.create_all(engine)
    migrations.migrate()
    assert {'student_account', 'student_login'} <= set(inspect(engine).get_table_names())
    engine.dispose()
