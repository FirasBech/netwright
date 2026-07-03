"""The API key must never appear in a serialized project or settings dict."""
import json

from core.project import NetwrightProject
from core.settings import Settings


def _contains_key_shape(blob: str) -> bool:
    return "sk-" in blob


def test_project_serialization_has_no_key(sample_topology):
    project = NetwrightProject(name="x", topology=sample_topology)
    blob = json.dumps(project.to_dict())
    assert not _contains_key_shape(blob)


def test_settings_as_dict_excludes_resolved_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-persisted")
    s = Settings()  # key is read from env at use-time, not stored
    assert not _contains_key_shape(json.dumps(s.as_dict()))
    # resolve_api_key reads env without persisting it
    assert s.resolve_api_key() == "sk-should-not-be-persisted"
    assert not _contains_key_shape(json.dumps(s.as_dict()))
