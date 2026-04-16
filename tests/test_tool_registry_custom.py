from __future__ import annotations

from pathlib import Path

from app.core.db import AppDB
from app.tools.registry import ToolRegistry


def test_import_and_execute_custom_tool_manifest(tmp_path: Path) -> None:
    db = AppDB(tmp_path / 'agent.db')
    registry = ToolRegistry(db)

    manifest = registry.import_manifest(
        {
            'name': 'custom_echo',
            'description': 'Echo wrapper',
            'risk_level': 'normal',
            'target_tool': 'echo',
            'default_params': {'message': 'default'},
            'param_mapping': {'text': 'message'},
            'version': '1.0.0',
        }
    )
    assert manifest.name == 'custom_echo'

    listed = {item['name']: item for item in registry.list_tools()}
    assert listed['custom_echo']['source'] == 'custom'

    result = registry.execute('custom_echo', {'text': 'hello custom'}, authorized=True)
    assert result['message'] == 'hello custom'

