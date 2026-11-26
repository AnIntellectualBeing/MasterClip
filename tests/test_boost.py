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


class FakeGemini:
    """Google answers 404 for a retired model id and 503 when one is busy: the next id must be tried."""
    def __init__(self, codes, text='{"moments": [{"id": "0", "score": 9, "title": "Roof jump goes wrong"}]}',
                 error="This model is currently experiencing high demand."):
        self.codes, self.text, self.error, self.calls = list(codes), text, error, []

    def post(self, url, headers=None, timeout=None, **kwargs):
        import requests

        model = url.split("/models/")[1].split(":")[0]
        self.calls.append((model, headers))
        code = self.codes.pop(0) if self.codes else 200
        if code == "slow":  # the model answers the request eventually, but queues past our wait
            raise requests.Timeout("read timed out")
        body = ({"error": {"code": code, "message": self.error}} if code >= 400
                else {"candidates": [{"content": {"parts": [{"text": self.text}]}}]})
        return SimpleNamespace(status_code=code, json=lambda: body, text=json.dumps(body))


def test_gemini_key_new_aq_format_is_accepted(tmp_path, vault):
    """aistudio.google.com now gives out “AQ.Ab8…” keys; the old “AIza only” check refused real keys."""
    b = Booster(tmp_path)
    key = "AQ.Ab8_FAKE_TEST_KEY_NOT_A_REAL_ONE_000000000000"
    b.save("gemini_api", key)
    assert vault["gemini_api_key"] == key and b.settings()["mode"] == "gemini_api"
    b.save("gemini_api", "AIza" + "x" * 35)  # the classic shape keeps working
    assert vault["gemini_api_key"].startswith("AIza")
    with pytest.raises(BoostError, match="Claude API key"):
        b.save("gemini_api", "sk-ant-" + "y" * 40)
    with pytest.raises(BoostError, match="aistudio"):
        b.save("gemini_api", "too-short")


def test_gemini_request_auth_header_matches_key_shape_and_skips_retired_models():
    """AQ… authorization keys are bearer tokens; classic AIza… keys ride the x-goog-api-key header."""
    session = FakeGemini([404, 503])
    answer = boost._ask_gemini_api("hi", "AQ.Ab8goodkeythatislongenough123", session=session)
    assert "Roof jump" in answer
    assert [model for model, _ in session.calls] == list(boost.GEMINI_MODELS[:3])
    assert session.calls[0][1] == {"Authorization": "Bearer AQ.Ab8goodkeythatislongenough123"}
    session = FakeGemini([])
    boost._ask_gemini_api("hi", "AIzagoodkeythatislongenough12345", session=session)
    assert session.calls[0][1] == {"x-goog-api-key": "AIzagoodkeythatislongenough12345"}


def test_gemini_auth_falls_back_to_the_other_header_once():
    """Google says no to one header (401/403/“API key not valid”): try the other flow before giving up."""
    session = FakeGemini([401])
    assert "Roof jump" in boost._ask_gemini_api("hi", "AQ.Ab8goodkeythatislongenough123", session=session)
    assert [headers for _, headers in session.calls] == [
        {"Authorization": "Bearer AQ.Ab8goodkeythatislongenough123"},
        {"x-goog-api-key": "AQ.Ab8goodkeythatislongenough123"},
    ]
    session = FakeGemini([403])
    assert "Roof jump" in boost._ask_gemini_api("hi", "AIzagoodkeythatislongenough12345", session=session)
    assert [headers for _, headers in session.calls] == [
        {"x-goog-api-key": "AIzagoodkeythatislongenough12345"},
        {"Authorization": "Bearer AIzagoodkeythatislongenough12345"},
    ]
    with pytest.raises(BoostError, match="didn't accept"):
        boost._ask_gemini_api("hi", "AQ.bad", session=FakeGemini([400, 400], error="API key not valid."))
    with pytest.raises(BoostError, match=r"had a problem \(503\)"):
        boost._ask_gemini_api("hi", "AQ.busy", session=FakeGemini([503] * len(boost.GEMINI_MODELS)))


def test_gemini_busy_model_falls_through_and_timeout_is_not_forever():
    """A model that queues past the wait must not lose the user's picks: try the next, lighter one."""
    session = FakeGemini(["slow", "slow"])
    assert "Roof jump" in boost._ask_gemini_api("hi", "AQ.Ab8goodkeythatislongenough123", session=session)
    assert [model for model, _ in session.calls] == list(boost.GEMINI_MODELS[:3])
    with pytest.raises(BoostError, match="queueing"):
        boost._ask_gemini_api("hi", "AQ.slow", session=FakeGemini(["slow"] * len(boost.GEMINI_MODELS)))


def test_parse_picks_takes_gemini_bare_list_and_single_row():
    """Gemini 3.x answers a bare [...] even with a JSON mime type, and sometimes one row on its own."""
    rows = '[{"id": "0", "score": 8.5, "title": "Roof jump fail"}]'
    assert boost.parse_picks(rows, {"0"}) == [Pick("0", 8, "Roof jump fail")]
    assert boost.parse_picks('{"id": "2", "score": 7, "title": "Whose car" }', {"2"}) == [Pick("2", 7, "Whose car")]
    assert boost.parse_picks('Here you go:\n' + rows + '\nHope that helps!', {"0"})[0].id == "0"
