"""Isolated, offline browser acceptance server. Never use for deployment."""
import os
from pathlib import Path
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='case-student-ui-') as folder:
        os.environ.update(DATABASE_URL='sqlite:///' + (Path(folder) / 'ui.db').as_posix(),
            ARTIFACT_ROOT=str(Path(folder) / 'artifacts'), CASE_SIM_LOG_FILE=str(Path(folder) / 'runtime.log'),
            TEACHER_PASSWORD='student-ui-test', LLM_API_KEY='', LLM_BASE_URL='', LLM_MODEL='')
        from backend.app.main import app
        from backend.app.db import SessionLocal
        from backend.app.db import engine
        from backend.app.seed import seed_demo_case
        from ai_component.agents import student_agent
        from ai_component.agents import tutor_agent
        import json
        import uvicorn
        async def review(messages, schema, retries, **kwargs):
            if 'passed' in schema:
                return {'passed': True, 'issues': []}
            from backend.app.domain.review_contract import required_dimensions
            return {'dimensions': [{'name': n, 'content': '离线验收模拟：根据你选择的渠道布局，讨论投入与现金流的平衡。'}
                for n in list(reversed(required_dimensions('4P营销理论'))) + ['执行建议', '风险提示']],
                'conclusion': '离线验收模拟：建议对比不同路径的营收与现金流。'}
        async def tutor(messages, schema, retries, **kwargs):
            if 'tools' in schema:
                return {'business': True, 'tools': ['read_case', 'read_results'], 'search_query': ''}
            # Confirms backend evidence actually arrived at the answer stage.
            assert json.loads(messages[1]['content'])['sources']
            return {'answer': '离线验收模拟：市场份额表示企业营收占目标市场规模的比例。[read_case]', 'source_ids': ['read_case']}
        student_agent.chat_json = review
        tutor_agent.chat_json = tutor
        with SessionLocal() as db:
            seed_demo_case(db)
            from backend.app.models import StudentAccount
            from backend.app.services.student_auth import hash_password
            db.add(StudentAccount(username='demo-student', real_name='演示学生', password_hash=hash_password('student-ui-test')))
            db.commit()
        try:
            uvicorn.run(app, host='127.0.0.1', port=int(os.getenv("CASE_SIM_TEST_PORT", "8198")), log_config=None)
        finally:
            engine.dispose()
            import logging
            for handler in list(logging.getLogger().handlers):
                if getattr(handler, '_case_sim', False):
                    handler.close()
                    logging.getLogger().removeHandler(handler)


if __name__ == '__main__':
    main()
