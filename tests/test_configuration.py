import io
import json
import run


def test_auto_director_only_selects_an_installed_local_model(monkeypatch):
    monkeypatch.setattr(
        run.urllib.request,
        "urlopen",
        lambda *args, **kwargs: io.BytesIO(
            json.dumps({"models": [{"name": "phi4-mini:latest"}]}).encode()
        ),
    )
    assert run.resolve_director_model("auto") == "phi4-mini:latest"


def test_missing_optional_director_does_not_require_setup(monkeypatch):
    def offline(*args, **kwargs):
        raise OSError("not running")

    monkeypatch.setattr(run.urllib.request, "urlopen", offline)
    assert run.resolve_director_model("auto") == ""
    assert run.resolve_director_model("my-model") == "my-model"
