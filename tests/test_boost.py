import json
from types import SimpleNamespace

import pytest

from clipstudio import boost
from clipstudio.boost import Booster, BoostError, Pick, blend, build_prompt, parse_picks


class FakeVault(dict):
    def get(self, name, default=None):
        return super().get(name, default)

    def put(self, name, value):
        self[name] = value

    def delete(self, name):
        self.pop(name, None)


@pytest.fixture
def vault(monkeypatch):
    fake = FakeVault()
    monkeypatch.setattr(boost.vault, "get", fake.get)
    monkeypatch.setattr(boost.vault, "put", fake.put)
    monkeypatch.setattr(boost.vault, "delete", fake.delete)
    return fake


MOMENTS = [
    {"id": "0", "start": 0, "end": 30, "score": 0.9, "text": "he jumped off the roof"},
    {"id": "1", "start": 40, "end": 70, "score": 0.95, "text": "checking my inventory"},
    {"id": "2", "start": 80, "end": 110, "score": 0.2, "text": "who stole my car"},
]


def test_prompt_has_ids_and_speech():
    text = build_prompt(MOMENTS, "Kai", "GTA RP")
    assert "Streamer: Kai" in text and "id 2 | 30s" in text and "who stole my car" in text


def test_parse_picks_from_text_and_dict():
    raw = 'Sure! {"moments":[{"id":"0","score":12,"title":" \\"Roof jump fail\\" "},{"id":"9","score":5,"title":"x"}]}'
    picks = parse_picks(raw, {"0", "1"})
    assert picks == [Pick("0", 10, "Roof jump fail")]
    with pytest.raises(BoostError):
        parse_picks({"moments": []}, {"0"})


def test_blend_prefers_ai_score_over_loudness():
    picks = {"0": Pick("0", 9, "a"), "1": Pick("1", 1, "b"), "2": Pick("2", 8, "c")}
    assert [m["id"] for m in blend(MOMENTS, picks, 2)] == ["0", "2"]


class FakeClient:
    def __init__(self, stop="end_turn"):
        self.calls = []
        self.stop = stop
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        body = {"moments": [{"id": "0", "score": 9, "title": "Roof jump goes wrong"}, {"id": "2", "score": 7, "title": "Who stole my car"}]}
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=json.dumps(body))])


def test_api_mode_request_and_key_in_vault(tmp_path, vault):
    client = FakeClient()
    b = Booster(tmp_path, api_client=client)
    with pytest.raises(BoostError, match="sk-ant-"):
        b.save("api", "not-a-key")
    with pytest.raises(BoostError, match="Paste"):
        b.save("api")
    b.save("api", "sk-ant-" + "x" * 40)
    assert "sk-ant" not in (tmp_path / "ai.json").read_text() and vault["anthropic_api_key"].startswith("sk-ant-")
    picks = b.rate(MOMENTS, "Kai")
    assert picks["0"].title == "Roof jump goes wrong" and "1" not in picks
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    status = b.status()
    assert status["has_key"] and "sk-ant" not in json.dumps(status)


def test_refusal_falls_back(tmp_path, vault):
    b = Booster(tmp_path, api_client=FakeClient(stop="refusal"))
    b.save("api", "sk-ant-" + "y" * 40)
    with pytest.raises(BoostError, match="declined"):
        b.rate(MOMENTS)


def test_off_by_default_and_forget_key(tmp_path, vault):
    b = Booster(tmp_path)
    assert b.settings() == {"mode": "off"} and not b.on
    b.save("api", "sk-ant-" + "z" * 40)
    b.forget_key()
    assert b.settings()["mode"] == "off" and "anthropic_api_key" not in vault
