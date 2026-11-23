"""Owner's 1.1 complaints: AI apps not found, Local AI error, auto-post vs minutes, blurry corner cam."""

import sys

import pytest

from clipstudio import boost
from clipstudio.design import cam_cover, template_design


def test_app_found_in_npm_folder_even_when_not_on_path(tmp_path, monkeypatch):
    npm = tmp_path / "npm"
    npm.mkdir()
    (npm / "codex.cmd").write_text("@echo off")
    monkeypatch.setattr(boost.shutil, "which", lambda name: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert boost.find_app("codex").endswith("codex.cmd")


def test_small_model_answer_shape_is_understood():
    picks = boost.parse_picks({"m1": {"score": 8, "title": "Police chase"}, "m2": {"score": 3, "title": "Drive"}}, {"m1", "m2"})
    assert [(p.id, p.score) for p in picks] == [("m1", 8), ("m2", 3)]


def test_local_mode_is_a_valid_setting():
    from clipstudio.app import BoostRequest

    assert BoostRequest(mode="local").mode == "local"


def test_huge_cam_detection_is_not_blurred_over_the_gameplay():
    d = template_design("reaction_corner", cam_roi=[0.2, 0.2, 0.5, 0.5])
    assert cam_cover(d, (0, 0, 1920, 1080), 1920, 1080) is None
    small = template_design("reaction_corner", cam_roi=[0.78, 0.41, 0.21, 0.3])
    assert cam_cover(small, (0, 0, 1920, 1080), 1920, 1080) is not None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: .exe apps and NTFS junctions")
def test_protected_app_folder_does_not_crash_ai_status(tmp_path, monkeypatch):
    """WinError 448 on Codex's bin folder crashed /api/ai, so no AI button worked (1.2.0)."""
    from pathlib import Path

    monkeypatch.setattr(boost.shutil, "which", lambda name: None)
    real = Path.is_file

    def protected(self):
        if "npm" in str(self):
            raise OSError(448, "untrusted mount point")
        return real(self)

    monkeypatch.setattr(Path, "is_file", protected)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    (tmp_path / "npm").mkdir()
    (tmp_path / "npm" / "codex.exe").write_text("x")
    assert boost.find_app("codex").endswith("codex.exe")
    assert boost._has("nothing-like-this") is False


def test_gpu_failure_mid_job_redoes_captions_on_cpu(monkeypatch):
    from clipstudio import transcript

    calls = []

    def run(*args, **kwargs):
        calls.append(transcript._GPU["ok"])
        if len(calls) == 1:
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return [{"text": "ok"}]

    monkeypatch.setattr(transcript, "_transcribe_run", run)
    monkeypatch.setitem(transcript._GPU, "ok", True)
    assert transcript.transcribe("a.wav") == [{"text": "ok"}]
    assert calls == [True, False]


def test_gpu_probe_runs_outside_the_engine(monkeypatch):
    from clipstudio import transcript

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd

        class R:
            returncode = 1

        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    assert transcript._probe_gpu("base") is False
    assert "gpu_probe_main" in " ".join(seen["cmd"])


def test_blocked_whole_stream_scan_falls_back_to_the_time_range(tmp_path, monkeypatch):
    """Friend, 23 Nov: 'The hot parts didn't download' ended the job. Now the shown range is used."""
    import threading

    import clipstudio.app as module
    from clipstudio import sources

    def blocked(*a, **k):
        raise sources.HotPartsFailed("YouTube is asking for a sign-in check right now.")

    seen = {}

    def import_url(url, folder, capture, event, progress, section=None, **k):
        seen["section"] = section
        raise RuntimeError("stop here")  # the rest of analyze is covered elsewhere

    monkeypatch.setattr(sources, "import_hotspots", blocked)
    monkeypatch.setattr(module, "import_url", import_url)
    job = module.store.create("QA", source_path=None, section={"hot": True, "fallback": [4277, 4877]})
    module.events[job["id"]] = threading.Event()
    module.analyze(job["id"], module.SourceRequest(source_type="url", source="https://www.youtube.com/watch?v=abcdefghijk"))
    assert seen["section"] == (4277.0, 4877.0)
    assert any("whole-stream scan" in w for w in module.store.get(job["id"])["warnings"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: .exe apps and NTFS junctions")
def test_codex_found_through_nested_junctions_without_walking_them(tmp_path, monkeypatch):
    """Owner, 23 Nov: Codex's bin is a junction to a junction; the installed app may not walk through them."""
    import subprocess
    from pathlib import Path

    real = tmp_path / "releases" / "0.1" / "bin"
    real.mkdir(parents=True)
    (real / "codex.exe").write_text("x")
    current = tmp_path / "current"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(current), str(tmp_path / "releases" / "0.1")], check=True, capture_output=True)
    link = tmp_path / "bin"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(current / "bin")], check=True, capture_output=True)
    try:
        resolved = boost.real_path(link / "codex.exe")
        assert resolved == real / "codex.exe"
        assert "current" not in str(resolved) and str(link) not in str(resolved)
    finally:
        import os

        os.rmdir(link)
        os.rmdir(current)


def test_buyer_can_paste_where_the_app_is(tmp_path, monkeypatch):
    monkeypatch.setattr(boost.shutil, "which", lambda name: None)
    monkeypatch.setattr(boost, "CUSTOM_PATHS", {})
    monkeypatch.setattr(boost, "_PATHS_FILE", [tmp_path / "ai-paths.json"])
    folder = tmp_path / "Weird Place"
    folder.mkdir()
    (folder / "codex.cmd").write_text("@echo off")
    import pytest

    with pytest.raises(boost.BoostError):
        boost.set_custom_path("codex", str(tmp_path / "nothing-here"))
    boost.set_custom_path("codex", f'"{folder}"')  # a folder, with quotes from Explorer's copy-as-path
    assert boost.find_app("codex").endswith("codex.cmd")
    assert "codex" in (tmp_path / "ai-paths.json").read_text()
