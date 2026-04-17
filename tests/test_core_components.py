from pathlib import Path

from app.core.agent import build_light_plan_exec, build_plan, detect_language, react_next_decision
from app.core.chat_graph import ChatGraphEngine
from app.core.langchain_adapter import LangChainAdapter
from app.core.config_store import AppSettings, ConfigStore, ModelConfig
from app.core.db import AppDB
from app.core.llm import LLMClient
from app.core.memory import MemoryManager
from app.tools.registry import ToolRegistry


def test_db_and_config_roundtrip(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    store = ConfigStore(db, tmp_path / 'secret.key')

    cfg = ModelConfig(provider='mock', model='mock-model', api_key='abc123')
    store.save_model_config(cfg)
    loaded = store.get_model_config()
    assert loaded.api_key == 'abc123'
    assert loaded.provider == 'mock'


def test_session_scoped_messages(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    db.add_message('user', 'hello s1', session_id='s1')
    db.add_message('assistant', 'reply s1', session_id='s1')
    db.add_message('user', 'hello s2', session_id='s2')

    s1 = db.list_messages(limit=10, session_id='s1')
    s2 = db.list_messages(limit=10, session_id='s2')
    assert len(s1) == 2
    assert len(s2) == 1
    assert s1[0]['content'] == 'hello s1'
    assert s2[0]['content'] == 'hello s2'


def test_wenxin_secret_key_roundtrip(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    store = ConfigStore(db, tmp_path / 'secret.key')

    cfg = ModelConfig(provider='wenxin', model='ernie-4.0-turbo-8k', api_key='ak', secret_key='sk')
    store.save_model_config(cfg)
    loaded = store.get_model_config()
    assert loaded.provider == 'wenxin'
    assert loaded.api_key == 'ak'
    assert loaded.secret_key == 'sk'


def test_app_settings_and_password_roundtrip(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    store = ConfigStore(db, tmp_path / 'secret.key')

    settings = AppSettings(theme='dark', language='en-US', log_lines=80, auto_refresh_logs=True)
    store.save_app_settings(settings)
    loaded_settings = store.get_app_settings()
    assert loaded_settings.theme == 'dark'
    assert loaded_settings.language == 'en-US'
    assert loaded_settings.log_lines == 80
    assert loaded_settings.auto_refresh_logs is True

    assert store.has_unlock_password() is False
    store.set_unlock_password('pass1234')
    assert store.has_unlock_password() is True
    assert store.verify_unlock_password('pass1234') is True
    assert store.verify_unlock_password('wrong') is False


def test_tool_registry_toggle(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    tools = ToolRegistry(db)
    tools.set_enabled('json_parse', False)
    listed = {t['name']: t for t in tools.list_tools()}
    assert listed['json_parse']['enabled'] is False


def test_planner_and_language() -> None:
    brain = build_plan('please search weather in beijing')
    assert brain.intent == 'search'
    assert brain.tool_calls[0].name == 'web_search'
    assert len(brain.plan) >= 2
    detected = detect_language('hello world')
    assert isinstance(detected, str)
    assert len(detected) >= 2


def test_multistep_search_and_save_plan() -> None:
    brain = build_plan('请搜索 FastAPI 教程并写入 "notes.txt"')
    assert brain.intent == 'search_and_save'
    assert len(brain.tool_calls) == 2
    assert brain.tool_calls[0].name == 'web_search'
    assert brain.tool_calls[1].name == 'write_file'
    assert brain.tool_calls[1].params['content'] == '{{tool:web_search}}'


def test_react_with_light_plan_exec() -> None:
    ctx = build_light_plan_exec('search FastAPI and save to "notes.txt"')
    assert ctx['mode'] == 'react+plan-exec'
    assert ctx['tool_calls']

    d1 = react_next_decision('search FastAPI and save to "notes.txt"', ctx, [])
    assert d1.action is not None
    assert isinstance(d1.thought, str)

    d2 = react_next_decision('search FastAPI and save to "notes.txt"', ctx, [{'tool': d1.action.name}])
    assert d2.action is not None

    done = react_next_decision(
        'search FastAPI and save to "notes.txt"',
        ctx,
        [{'tool': 'web_search'}, {'tool': 'write_file'}],
    )
    assert done.should_stop is True


def test_skill_version_history_and_rollback(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    s1 = {'name': 'db_skill', 'version': '1.0.0', 'steps': []}
    skill_id = db.upsert_skill('db_skill', '1.0.0', s1)
    s2 = {'name': 'db_skill', 'version': '2.0.0', 'steps': [{'kind': 'llm', 'name': 'n'}]}
    db.upsert_skill('db_skill', '2.0.0', s2, source='import')

    versions = db.list_skill_versions(skill_id)
    assert len(versions) >= 2
    oldest_version_id = versions[-1]['version_id']

    out = db.rollback_skill(skill_id, oldest_version_id, 'unit-test')
    assert out['rollback_to_version'] == '1.0.0'
    active = db.get_skill(skill_id)
    assert active is not None
    assert active['version'] == '1.0.0'


def test_memory_manager_retrieve_and_update(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    mgr = MemoryManager(db)

    db.add_message('user', '我喜欢简洁回答', session_id='demo')
    db.add_message('assistant', '好的，我会简洁。', session_id='demo')

    upd = mgr.update_after_turn(
        user_message='我的项目路径是 D:/work/demo',
        assistant_reply='已记录你的项目路径。',
        session_id='demo',
        tool_trace=[{'tool': 'echo'}],
    )
    assert 'state' in upd

    out = mgr.retrieve_context('项目路径是什么', session_id='demo')
    assert 'short_term' in out
    assert 'long_term' in out
    assert isinstance(out['context_messages'], list)


def test_langgraph_engine_mock_run_if_available(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    tools = ToolRegistry(db)
    adapter = LangChainAdapter(llm_client=LLMClient(), tool_registry=tools)
    engine = ChatGraphEngine(adapter=adapter, memory_manager=MemoryManager(db))
    if not engine.available:
        assert engine.available is False
        return

    out = engine.run(
        message='hello graph',
        cfg=ModelConfig(provider='mock', model='mock-model'),
        session_id='graph-test',
        force_tool=None,
        tool_params={},
        enabled_tools=[t for t in tools.list_tools() if t.get('enabled', True)],
        is_mock=True,
    )
    assert isinstance(out.get('final_reply'), str)


