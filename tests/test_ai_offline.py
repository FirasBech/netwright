from ai.assistant import explain
from ai.client import NetwrightAI


def test_unavailable_without_key_or_sdk():
    ai = NetwrightAI(api_key=None)
    # No injected client, no key in env (conftest unsets it).
    assert ai.available() is False


def test_available_with_injected_client():
    ai = NetwrightAI(client=object())
    assert ai.available() is True


def test_templated_explain_is_deterministic_offline(sample_topology):
    text = explain(sample_topology, ai=None)
    assert "device" in text.lower()
    assert "VLAN" in text
    # deterministic: same input -> same output
    assert explain(sample_topology, ai=None) == text


def test_explain_mentions_validation(sample_topology):
    text = explain(sample_topology, ai=None)
    assert "Validation" in text
