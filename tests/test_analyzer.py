import pytest

from app.adapters.analyzer import (
    DEFAULT_OPENAI_MODEL,
    DeterministicAnalyzer,
    OpenAIResponsesAnalyzer,
    create_analyzer,
    load_local_env,
)
from app.services.catalog import load_catalog


class FakeResponses:
    def __init__(self, output): self.output, self.calls = output, []
    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return type("Response", (), {"output_parsed": self.output})()


class FakeClient:
    def __init__(self, output): self.responses = FakeResponses(output)


class FakeChatCompletions:
    def __init__(self, output): self.output, self.calls = output, []
    def parse(self, **kwargs):
        self.calls.append(kwargs)
        message = type("Message", (), {"parsed": self.output})()
        return type("Completion", (), {"choices": [type("Choice", (), {"message": message})()]})()


class FakeChatClient:
    def __init__(self, output):
        completions = FakeChatCompletions(output)
        self.chat = type("Chat", (), {"completions": completions})()


def test_deterministic_analyzer_finds_listing_inventory_and_order_risks():
    products, inventory, orders = load_catalog()
    advice = {row.sku: row for row in DeterministicAnalyzer().analyze(products, inventory, orders)}
    assert advice["SKU-001"].score < 100
    assert advice["SKU-001"].issues
    assert advice["SKU-009"].inventory_risk
    assert advice["SKU-017"].order_risk
    assert len(advice["SKU-001"].suggested_bullets) == 5


def test_openai_responses_success_is_validated_without_network():
    products, inventory, orders = load_catalog()
    expected = DeterministicAnalyzer().analyze(products, inventory, orders)
    client = FakeClient({"advice": [item.model_dump() for item in expected]})
    analyzer = OpenAIResponsesAnalyzer(api_key="test-key", model=DEFAULT_OPENAI_MODEL, api_mode="responses", client=client)
    assert analyzer.analyze(products, inventory, orders) == expected
    assert client.responses.calls[0]["model"] == DEFAULT_OPENAI_MODEL


def test_openai_chat_completions_success_is_validated_without_network():
    products, inventory, orders = load_catalog()
    expected = DeterministicAnalyzer().analyze(products, inventory, orders)
    client = FakeChatClient({"advice": [item.model_dump() for item in expected]})
    analyzer = OpenAIResponsesAnalyzer(api_key="test-key", model="gpt-4o-mini", api_mode="chat_completions", client=client)
    assert analyzer.analyze(products, inventory, orders) == expected
    assert analyzer.provider == "openai_chat_completions"
    assert client.chat.completions.calls[0]["response_format"].__name__ == "ListingAdviceBatch"


def test_openai_responses_rejects_bad_sku_output_without_network():
    products, inventory, orders = load_catalog()
    duplicate = DeterministicAnalyzer().analyze(products, inventory, orders)[0].model_dump()
    analyzer = OpenAIResponsesAnalyzer(api_key="test-key", api_mode="responses", client=FakeClient({"advice": [duplicate, duplicate, duplicate]}))
    with pytest.raises(RuntimeError, match="exactly one"):
        analyzer.analyze(products, inventory, orders)


def test_openai_responses_rejects_invalid_advice_content_without_network():
    products, inventory, orders = load_catalog()
    invalid = [item.model_dump() for item in DeterministicAnalyzer().analyze(products, inventory, orders)]
    invalid[0]["suggested_bullets"] = ["", "two", "three", "four", "five"]
    analyzer = OpenAIResponsesAnalyzer(api_key="test-key", api_mode="responses", client=FakeClient({"advice": invalid}))
    with pytest.raises(RuntimeError, match="invalid structured output"):
        analyzer.analyze(products, inventory, orders)


def test_openai_missing_key_makes_zero_network_calls(monkeypatch):
    products, inventory, orders = load_catalog()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    analyzer = OpenAIResponsesAnalyzer(api_key=None, client=type("Client", (), {"responses": None})())
    monkeypatch.setattr(analyzer, "_client", lambda: pytest.fail("network client must not be created"))
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        analyzer.analyze(products, inventory, orders)


def test_provider_selection_and_shell_env_precedence(tmp_path, monkeypatch):
    env_file = tmp_path / "test.env"
    env_file.write_text("OPENAI_MODEL=file-model\nNEW_TEST_VALUE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "shell-model")
    monkeypatch.delenv("NEW_TEST_VALUE", raising=False)
    load_local_env(env_file)
    assert OpenAIResponsesAnalyzer(api_key="test-key").model == "shell-model"
    assert create_analyzer("deterministic").mode == "mock"
    assert create_analyzer("openai").mode == "openai"
    assert create_analyzer("mock").provider == "deterministic"


def test_openai_api_mode_rejects_unknown_value():
    with pytest.raises(ValueError, match="OPENAI_API_MODE"):
        OpenAIResponsesAnalyzer(api_key="test-key", api_mode="unknown")
