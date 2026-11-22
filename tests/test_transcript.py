import json
from pathlib import Path
import wave

import pytest

from clipstudio.transcript import (
    TranscriptCancelled,
    TranscriptError,
    _load_model,
    map_transcript_to_clips,
    map_transcript_to_concatenation,
    parse_transcript,
    segments_to_srt,
    transcribe,
    write_clip_srt,
)


def _write_silence_wav(path, seconds=5):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\0\0" * 16000 * seconds)


def _fake_extract_audio(source, output, sample_rate=16000, cancel_event=None):
    _write_silence_wav(output)
    return Path(output)


def test_parse_srt_normalizes_cues_and_text():
    parsed = parse_transcript(
        """1
00:00:01,250 --> 00:00:02,750
Hello <i>there</i>!

2
00:00:03,000 --> 00:00:04,000
Namaste
""",
        format="srt",
    )
    assert parsed == [
        {"start": 1.25, "end": 2.75, "text": "Hello there!"},
        {"start": 3.0, "end": 4.0, "text": "Namaste"},
    ]


def test_parse_json_path_and_reject_invalid_segments(tmp_path):
    source = tmp_path / "captions.json"
    source.write_text(
        json.dumps(
            {
                "segments": [
                    {
                        "start": 0,
                        "end": 1.5,
                        "text": "Hi",
                        "words": [
                            {"start": 0.2, "end": 0.6, "text": "Hi", "probability": 0.9}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert parse_transcript(source) == [
        {
            "start": 0.0,
            "end": 1.5,
            "text": "Hi",
            "words": [
                {"start": 0.2, "end": 0.6, "text": "Hi", "probability": 0.9}
            ],
        }
    ]
    with pytest.raises(TranscriptError, match="end"):
        parse_transcript('[{"start": 2, "end": 1, "text": "bad"}]', format="json")


def test_srt_roundtrip_format_and_millisecond_rounding():
    cues = [{"start": 1.2346, "end": 2.0, "text": "hello\nworld"}]
    rendered = segments_to_srt(cues)
    assert rendered == ("1\n00:00:01,235 --> 00:00:02,000\nhello world\n")
    assert parse_transcript(rendered, format="srt")[0] == {
        "start": pytest.approx(1.235),
        "end": 2.0,
        "text": "hello world",
    }


def test_malformed_srt_is_rejected():
    with pytest.raises(TranscriptError, match="timestamp"):
        parse_transcript("1\n00:99:00,000 --> 00:00:01,000\nBad\n", format="srt")


def test_map_segments_into_concatenated_clip_local_times():
    segments = [
        {"start": 8.0, "end": 11.0, "text": "one"},
        {"start": 13.0, "end": 16.0, "text": "two"},
    ]
    clips = [{"start": 9.0, "end": 10.0}, {"start": 14.0, "end": 16.0}]
    assert map_transcript_to_clips(segments, clips) == [
        [{"start": 0.0, "end": 1.0, "text": "one"}],
        [{"start": 0.0, "end": 2.0, "text": "two"}],
    ]
    assert (
        write_clip_srt(segments, clips[0]) == "1\n00:00:00,000 --> 00:00:01,000\none\n"
    )


def test_map_word_timed_transcript_trims_text_to_words_inside_clip():
    words = [
        {"start": 0.0, "end": 0.4, "text": "Before", "probability": 0.9},
        {"start": 1.0, "end": 1.3, "text": "game", "probability": 0.9},
        {"start": 1.3, "end": 1.6, "text": "starts", "probability": 0.9},
        {"start": 1.6, "end": 1.9, "text": "now", "probability": 0.9},
        {"start": 2.0, "end": 2.5, "text": "outside", "probability": 0.8},
    ]
    mapped = map_transcript_to_clips(
        [{"start": 0.0, "end": 2.5, "text": "Before game starts now outside", "words": words}],
        [{"start": 1.2, "end": 2.1}],
    )
    cue = mapped[0][0]
    assert (cue["start"], cue["end"]) == pytest.approx((0.0, 0.9))
    assert cue["text"] == "game starts now outside"
    assert [word["text"] for word in cue["words"]] == ["game", "starts", "now", "outside"]
    for word, bounds in zip(cue["words"], [(0.0, 0.1), (0.1, 0.4), (0.4, 0.7), (0.8, 0.9)]):
        assert (word["start"], word["end"]) == pytest.approx(bounds)


def test_concatenation_offsets_word_times_and_does_not_repeat_cut_text():
    segment = {
        "start": 0.0,
        "end": 4.0,
        "text": "first pause second",
        "words": [
            {"start": 0.2, "end": 0.8, "text": "first", "probability": 0.9},
            {"start": 2.2, "end": 2.8, "text": "second", "probability": 0.9},
        ],
    }
    clips = [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 3.0}]
    mapped = map_transcript_to_concatenation([segment], clips)
    assert [cue["text"] for cue in mapped] == ["first", "second"]
    assert mapped[0]["words"][0]["start"] == pytest.approx(0.2)
    assert mapped[1]["words"][0]["start"] == pytest.approx(1.2)


def test_transcribe_reports_segment_progress_and_returns_plain_dicts(
    tmp_path, monkeypatch
):
    _load_model.cache_clear()
    monkeypatch.setattr("clipstudio.transcript.extract_audio", _fake_extract_audio)

    class Segment:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class Info:
        duration = 5.0

    class Model:
        def __init__(self, model_size, device, compute_type, cpu_threads):
            assert (model_size, device, compute_type) == ("base", "cpu", "int8")
            assert cpu_threads == 4

        def transcribe(
            self, samples, language, beam_size, vad_filter, condition_on_previous_text,
            word_timestamps, task="transcribe",
        ):
            assert task == "transcribe"
            assert len(samples) == 16000 * 5
            assert language is None
            assert vad_filter is True
            assert condition_on_previous_text is False
            assert word_timestamps is True
            return iter([Segment(0, 2, " hello "), Segment(2, 5, " नमस्ते ")]), Info()

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        type("M", (), {"WhisperModel": Model}),
    )
    audio = tmp_path / "audio.wav"
    audio.touch()
    updates = []
    result = transcribe(
        audio, progress=lambda current, total: updates.append((current, total))
    )
    assert result == [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 2.0, "end": 5.0, "text": "नमस्ते"},
    ]
    assert updates == [(2.0, 5.0), (5.0, 5.0), (5.0, 5.0)]


def test_transcribe_pre_cancelled_does_not_import_model(tmp_path, monkeypatch):
    import builtins
    import threading

    audio = tmp_path / "audio.wav"
    audio.touch()
    cancelled = threading.Event()
    cancelled.set()
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise AssertionError("must honor cancellation before importing model")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(TranscriptCancelled):
        transcribe(audio, cancel_event=cancelled)


def test_transcribe_checks_cancellation_between_segments(tmp_path, monkeypatch):
    import threading

    _load_model.cache_clear()
    monkeypatch.setattr("clipstudio.transcript.extract_audio", _fake_extract_audio)

    class Segment:
        start, end, text = 0.0, 1.0, "hello"

    class Info:
        duration = 3.0

    cancelled = threading.Event()

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            def generate():
                yield Segment()
                cancelled.set()
                yield Segment()

            return generate(), Info()

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        type("M", (), {"WhisperModel": Model}),
    )
    audio = tmp_path / "audio.wav"
    audio.touch()
    with pytest.raises(TranscriptCancelled):
        transcribe(audio, cancel_event=cancelled)


def test_transcribe_chunks_long_input_and_offsets_segment_times(tmp_path, monkeypatch):
    import numpy as np

    _load_model.cache_clear()
    monkeypatch.setattr("clipstudio.transcript._CHUNK_SECONDS", 1)

    def three_second_wav(source, output, sample_rate=16000, cancel_event=None):
        _write_silence_wav(output, seconds=3)
        return Path(output)

    monkeypatch.setattr("clipstudio.transcript.extract_audio", three_second_wav)

    class Segment:
        start, end, text = 0.25, 1.5, "word"

    class Info:
        duration = 1.0

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, samples, **kwargs):
            assert isinstance(samples, np.ndarray)
            assert len(samples) == 16000
            return iter([Segment()]), Info()

    monkeypatch.setitem(
        __import__("sys").modules,
        "faster_whisper",
        type("M", (), {"WhisperModel": Model}),
    )
    audio = tmp_path / "audio.wav"
    audio.touch()
    result = transcribe(audio)
    assert result == [
        {"start": 0.25, "end": 1.0, "text": "word"},
        {"start": 1.25, "end": 2.0, "text": "word"},
        {"start": 2.25, "end": 3.0, "text": "word"},
    ]


def test_transcribe_uses_word_bounds_for_broad_single_word_segments(tmp_path, monkeypatch):
    _load_model.cache_clear()
    monkeypatch.setattr("clipstudio.transcript.extract_audio", _fake_extract_audio)

    class Word:
        start, end, word, probability = 1.2, 1.6, "Okay.", 0.95

    class Segment:
        start, end, text = 0.0, 5.0, "Okay."
        avg_logprob, no_speech_prob, compression_ratio = -0.2, 0.01, 1.1
        words = [Word()]

    class Info:
        duration = 5.0

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            assert kwargs["word_timestamps"] is True
            return iter([Segment()]), Info()

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("M", (), {"WhisperModel": Model}))
    audio = tmp_path / "audio.wav"
    audio.touch()
    assert transcribe(audio) == [
        {
            "start": 1.2,
            "end": 1.6,
            "text": "Okay.",
            "words": [{"start": 1.2, "end": 1.6, "text": "Okay.", "probability": 0.95}],
        }
    ]


@pytest.mark.parametrize(
    ("avg_logprob", "no_speech_prob", "compression_ratio"),
    [
        (-1.2, 0.01, 1.1),
        (-0.2, 0.9, 1.1),
        (-0.2, 0.01, 3.0),
    ],
)
def test_transcribe_drops_low_confidence_sparse_hallucinations(
    tmp_path, monkeypatch, avg_logprob, no_speech_prob, compression_ratio
):
    _load_model.cache_clear()
    monkeypatch.setattr("clipstudio.transcript.extract_audio", _fake_extract_audio)

    class Segment:
        start, end, text = 0.0, 2.0, "Cool."
        words = []

    Segment.avg_logprob = avg_logprob
    Segment.no_speech_prob = no_speech_prob
    Segment.compression_ratio = compression_ratio

    class Info:
        duration = 5.0

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([Segment()]), Info()

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("M", (), {"WhisperModel": Model}))
    audio = tmp_path / "audio.wav"
    audio.touch()
    updates = []
    assert transcribe(audio, progress=lambda current, total: updates.append((current, total))) == []
    assert updates[-1] == (5.0, 5.0)


def test_transcribe_drops_single_word_spanning_40_seconds(tmp_path, monkeypatch):
    _load_model.cache_clear()

    def long_silence(source, output, sample_rate=16000, cancel_event=None):
        _write_silence_wav(output, seconds=120)
        return Path(output)

    monkeypatch.setattr("clipstudio.transcript.extract_audio", long_silence)

    class Segment:
        start, end, text = 63.3, 99.75, "Cool."
        avg_logprob, no_speech_prob, compression_ratio = -0.2, 0.01, 1.1
        words = []

    class Info:
        duration = 120.0

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([Segment()]), Info()

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("M", (), {"WhisperModel": Model}))
    audio = tmp_path / "audio.wav"
    audio.touch()
    assert transcribe(audio) == []


def test_bundled_speech_model_is_used_first(tmp_path, monkeypatch):
    from clipstudio import transcript

    monkeypatch.setattr(transcript, "BUNDLED_DIR", tmp_path)
    assert transcript.bundled_model("base") is None
    folder = tmp_path / "whisper-base"
    folder.mkdir()
    for name in transcript.MODEL_FILES:
        (folder / name).write_text("x")
    assert transcript.bundled_model("base") == folder
    assert transcript.model_status("base") == "bundled"
