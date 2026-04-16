from pathlib import Path

from app.core.agent import build_light_plan_exec, build_plan, detect_language, react_next_decision
from app.core.config_store import AppSettings, ConfigStore, ModelConfig
from app.core.db import AppDB
from app.tools.registry import ToolRegistry


def test_db_and_config_roundtrip(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    store = ConfigStore(db, tmp_path / 'secret.key')

    cfg = ModelConfig(provider='mock', model='mock-model', api_key='abc123')
    store.save_model_config(cfg)
    loaded = store.get_model_config()
    assert loaded.api_key == 'abc123'
    assert loaded.provider == 'mock'


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


