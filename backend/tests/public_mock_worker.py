"""Disposable public gateway acceptance worker. No credentials or real AI calls."""
import os
os.environ.update(LLM_API_KEY="", LLM_BASE_URL="", LLM_MODEL="", DEEPSEEK_API_KEY="", TAVILY_API_KEY="")
from backend.app.main import app
from ai_component.agents import generator, student_agent, tutor_agent
from backend.app.domain import rules
from backend.app.domain.review_contract import required_dimensions


async def generate(messages, schema, retries, **kw):
    source = rules.SAMPLE_CASE
    result_map = {(r["node_index"], r["option_key"]): r for r in source["simulation_results"]}
    return {"title": source["title"], "background": source["background"], "dilemma": source["dilemma"],
        "base_metrics": {"revenue": source["base_metrics"]["monthly_revenue_per_store"],
            "gross_margin": source["base_metrics"]["gross_margin"], "market_share": source["base_metrics"]["market_share"], "cash_flow": "稳健"},
        "nodes": [{"idx": i, "node_role": n["node_role"], "title": n["title"], "background": n["background"],
            "options": [{"key": o["option_key"], "label": o["label"], "risk_level": o["risk_level"],
                "metrics": result_map[(i, o["option_key"])]["metrics"], "summary": result_map[(i, o["option_key"])]["summary"]}
                for o in n["options"]]} for i, n in enumerate(source["nodes"], 1)]}


async def review(messages, schema, retries, **kw):
    if "passed" in schema:
        return {"passed": True, "issues": []}
    return {"dimensions": [{"name": n, "content": "模拟验收：根据已保存的决策讨论投入与现金流。"}
        for n in required_dimensions("4P营销理论")], "conclusion": "模拟验收：建议关注现金流与市场变化。"}


async def tutor(messages, schema, retries, **kw):
    if "tools" in schema:
        return {"business": True, "tools": ["read_case", "read_results"], "search_query": ""}
    return {"answer": "模拟验收：市场份额体现企业在目标市场的占比。[read_case]", "source_ids": ["read_case"]}


generator.chat_json = generate
student_agent.chat_json = review
tutor_agent.chat_json = tutor
