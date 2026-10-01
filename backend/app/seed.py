"""Idempotently seed the bundled benchmark case without calling external services."""
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import models
from backend.app.domain import rules
from backend.app.db import SessionLocal

SEED_STUDENT_TOKEN = "benchmark-demo-v4"
SEED_TEACHER_TOKEN = "benchmark-seed-owner"


def seed_demo_case(db: Session) -> models.Case:
    existing = db.scalar(select(models.Case).where(models.Case.student_token == SEED_STUDENT_TOKEN))
    if existing is not None:
        return existing

    source: dict[str, Any] = rules.SAMPLE_CASE
    framework = db.scalar(select(models.Framework).where(models.Framework.name == source["framework"]))
    if framework is None:
        framework = models.Framework(
            name=source["framework"], prompt_template="Bundled benchmark framework",
            output_schema=source["standard_review"], version="v1",
        )
        db.add(framework)
        db.flush()

    raw_base = source["base_metrics"]
    case = models.Case(
        title=source["title"], source_text=source["background"], status="published",
        framework_id=framework.id, teacher_token=SEED_TEACHER_TOKEN,
        student_token=SEED_STUDENT_TOKEN, published_at=datetime.now(),
        case_type=source["case_type"], source_kind="material", owner_type="teacher",
        background=source["background"], dilemma=source["dilemma"], version=1,
        base_metrics_json={
            "revenue": raw_base["monthly_revenue_per_store"],
            "gross_margin": raw_base["gross_margin"],
            "market_share": raw_base["market_share"],
            "cash_flow": "稳健",
        },
    )
    db.add(case)
    db.flush()
    results = {(item["node_index"], item["option_key"]): item for item in source["simulation_results"]}
    for index, raw_node in enumerate(source["nodes"], start=1):
        node = models.Node(
            case_id=case.id, idx=index, scenario=raw_node["background"],
            node_role=raw_node["node_role"], title=raw_node["title"],
            background=raw_node["background"], options_json=raw_node["options"],
        )
        db.add(node)
        db.flush()
        for raw_option in raw_node["options"]:
            result = results[(index, raw_option["option_key"])]
            db.add(models.OptionResult(
                node_id=node.id, option_key=raw_option["option_key"], label=raw_option["label"],
                risk_level=raw_option["risk_level"], metrics_json=result["metrics"],
                summary=result["summary"],
            ))
    db.commit()
    db.refresh(case)
    return case


def main() -> None:
    with SessionLocal() as db:
        case = seed_demo_case(db)
        print(f"Seed ready: case_id={case.id}, student_token={case.student_token}")


if __name__ == "__main__":
    main()
