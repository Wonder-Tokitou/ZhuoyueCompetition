"""Explicit live-provider smoke test on isolated stage only; no business writes."""
import asyncio
import json
from pathlib import Path
from sqlalchemy import select
from backend.app.db import SessionLocal
from backend.app import models
from backend.app.integrations.ai.impact_rules import context, validate_candidate
from ai_component.agents.impact_rules import generate_impact_rules


async def main():
    assert Path.cwd() == Path('/tmp/case-sim-impact-stage')
    with SessionLocal() as db:
        case = db.scalar(select(models.Case).order_by(models.Case.id))
        data = context(case)
    error = ''
    for _ in range(2):
        candidate = await generate_impact_rules(data, error)
        try:
            output = validate_candidate(data, candidate)
        except (ValueError, TypeError, KeyError) as exc:
            error = str(exc)
            continue
        print(json.dumps({'live_deepseek': 'passed', 'rules': len(output['rules']),
            'paths': output['validated_paths'], 'cash_baseline': output['cash_flow_amount'],
            'business_data_written': False}, ensure_ascii=False))
        return
    raise RuntimeError(error)


if __name__ == '__main__':
    asyncio.run(main())
