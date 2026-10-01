from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base
from backend.app.integrations.ai import artifacts
from ai_component.providers.llm import _settings


def test_ai_tables_and_local_artifact_store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ai.db'}")
    Base.metadata.create_all(engine)
    assert {"ai_task", "ai_task_step", "ai_artifact"}.issubset(
        set(inspect(engine).get_table_names())
    )
    monkeypatch.setattr(artifacts, "ARTIFACT_ROOT", Path(tmp_path) / "artifacts")
    with sessionmaker(bind=engine)() as db:
        row = artifacts.artifact_store.save_json(db, {"ok": True}, kind="validation_report")
        db.commit()
        assert row.size > 0
        assert row.sha256
        assert (Path(tmp_path) / "artifacts").exists()


def test_deepseek_uses_openai_compatible_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/")
    monkeypatch.setenv("LLM_API_KEY", "test-only")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")
    settings = _settings()
    assert settings["provider"] == "openai_compatible"
    assert settings["base"] == "https://api.deepseek.com"
    assert settings["model"] == "deepseek-flash"
