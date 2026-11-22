"""Local speech transcription and subtitle timeline helpers.

No network service is used: ``faster-whisper`` loads its model locally. The
caller owns caching of results; inference is serialized to bound CPU/model
memory use in desktop applications.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import threading
import wave
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .media import extract_audio


class TranscriptError(ValueError):
    """Invalid transcript input or unavailable local transcription support."""


class TranscriptCancelled(TranscriptError):
    """Raised when a caller cancels local transcription."""


_MODEL_LOCK = threading.BoundedSemaphore(1)
_MODEL_CACHE_LOCK = threading.Lock()
_CHUNK_SECONDS = 600
_SAMPLE_RATE = 16000
_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})$")
_TAG = re.compile(r"<[^>]*>")


def _timestamp_seconds(value: str) -> float:
    match = _TIMESTAMP.fullmatch(value.strip())
    if not match:
        raise TranscriptError(f"Invalid SRT timestamp: {value!r}")
    hours, minutes, seconds, millis = match.groups()
    h, m, s = int(hours or 0), int(minutes), int(seconds)
    if m > 59 or s > 59:
        raise TranscriptError(f"Invalid SRT timestamp: {value!r}")
    return h * 3600 + m * 60 + s + int(millis.ljust(3, "0")) / 1000


def _clean_segment(segment) -> dict:
    if not isinstance(segment, dict):
        raise TranscriptError("Each transcript segment must be an object.")
    try:
        start, end = float(segment["start"]), float(segment["end"])
        text = str(segment.get("text", "")).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise TranscriptError("Each segment needs numeric start/end times.") from exc
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise TranscriptError(
            "Transcript segment end must be greater than its finite, nonnegative start."
        )
    cleaned = {"start": start, "end": end, "text": text}
    if "words" in segment:
        words = segment["words"]
        if not isinstance(words, list):
            raise TranscriptError("Segment words must be an array.")
        cleaned_words = sorted(
            (_clean_word(word) for word in words), key=lambda word: word["start"]
        )
        if cleaned_words:
            cleaned["words"] = cleaned_words
    return cleaned


def _clean_word(word) -> dict:
    if not isinstance(word, dict):
        raise TranscriptError("Each transcript word must be an object.")
    try:
        start, end = float(word["start"]), float(word["end"])
        text = str(word.get("text", "")).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise TranscriptError("Each word needs numeric start/end times.") from exc
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or not text:
        raise TranscriptError("Transcript word needs text and a finite, positive time range.")
    cleaned = {"start": start, "end": end, "text": text}
    if "probability" in word and word["probability"] is not None:
        try:
            probability = float(word["probability"])
        except (TypeError, ValueError) as exc:
            raise TranscriptError("Word probability must be numeric.") from exc
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise TranscriptError("Word probability must be between zero and one.")
        cleaned["probability"] = probability
    return cleaned


def _parse_srt(text: str) -> list[dict]:
    output = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.strip().splitlines()
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        timing = lines[timing_index].split("-->", 1)
        if len(timing) != 2:
            raise TranscriptError("Invalid SRT timing line.")
        start_text = timing[0].strip()
        end_text = timing[1].strip().split()[0]
        start, end = _timestamp_seconds(start_text), _timestamp_seconds(end_text)
        caption = " ".join(
            _TAG.sub("", line).strip() for line in lines[timing_index + 1 :]
        ).strip()
        output.append(_clean_segment({"start": start, "end": end, "text": caption}))
    if not output and text.strip():
        raise TranscriptError("No valid SRT captions found.")
    return output


def parse_transcript(path_or_text: str | Path, format: str | None = None) -> list[dict]:
    """Read a JSON segment array/object or SRT file/text and validate its cues."""
    if isinstance(path_or_text, Path):
        raw = path_or_text.read_text(encoding="utf-8-sig")
        selected_format = format or path_or_text.suffix.lstrip(".")
    else:
        candidate = Path(path_or_text)
        if format is None and "\n" not in path_or_text and candidate.is_file():
            raw = candidate.read_text(encoding="utf-8-sig")
            selected_format = candidate.suffix.lstrip(".")
        else:
            raw = path_or_text
            selected_format = format
    selected_format = (
        selected_format or ("json" if raw.lstrip().startswith(("{", "[")) else "srt")
    ).lower()
    if selected_format == "srt":
        return _parse_srt(raw)
    if selected_format not in ("json", "js"):
        raise TranscriptError("Transcript format must be SRT or JSON.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranscriptError("Invalid transcript JSON.") from exc
    if isinstance(data, dict):
        data = data.get("segments")
    if not isinstance(data, list):
        raise TranscriptError(
            "JSON transcript must be an array or an object with a segments array."
        )
    return [_clean_segment(item) for item in data]


LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "te": "Telugu", "ml": "Malayalam",
    "mr": "Marathi", "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu", "es": "Spanish",
    "pt": "Portuguese", "fr": "French", "de": "German", "ar": "Arabic", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "id": "Indonesian", "tr": "Turkish", "it": "Italian",
}
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
BUNDLED_DIR = Path(__file__).resolve().parent.parent / "models"


def bundled_model(model_size: str) -> Path | None:
    """The speech model shipped inside the installer (models/whisper-<size>), if it is there."""
    folder = BUNDLED_DIR / f"whisper-{model_size}"
    return folder if all((folder / f).is_file() for f in MODEL_FILES) else None


def model_status(model_size: str = "base") -> str:
    """'bundled' (inside the app, works offline), 'downloaded' (cached from an earlier run),
    or 'download' (fetched once, ~150 MB, the first time something is transcribed)."""
    if bundled_model(model_size):
        return "bundled"
    try:
        from faster_whisper.utils import download_model

        download_model(model_size, local_files_only=True)
        return "downloaded"
    except Exception:
        return "download"


_GPU = {"ok": None}


def _whisper(ref):
    """An NVIDIA GPU (with its CUDA libraries) makes speech ~5x faster; anything else uses the CPU."""
    from faster_whisper import WhisperModel

    if _GPU["ok"] is not False:
        try:
            import ctranslate2
            import numpy as np

            if ctranslate2.get_cuda_device_count() > 0:
                model = WhisperModel(str(ref), device="cuda", compute_type="float16")
                list(model.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])  # proves the libraries load
                _GPU["ok"] = True
                return model
        except Exception:
            pass
        _GPU["ok"] = False
    return WhisperModel(str(ref), device="cpu", compute_type="int8", cpu_threads=4)


def gpu_in_use() -> bool:
    return bool(_GPU["ok"])


@lru_cache(maxsize=1)
def _load_model(model_size: str):
    local = bundled_model(model_size)
    if local:
        return _whisper(local)
    model_ref = model_size
    try:
        from faster_whisper.utils import download_model
        from huggingface_hub.errors import LocalEntryNotFoundError
        try:
            model_ref = download_model(model_size, local_files_only=True)
        except LocalEntryNotFoundError:
            # A cache miss may use faster-whisper's normal model-name download.
            pass
    except ImportError:
        pass
    return _whisper(model_ref)


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptCancelled("Transcription cancelled.")


# Languages Whisper "base" hears poorly (Hindi, Kannada, Tamil, ...) get the "small" model, fetched
# once (~480 MB) the first time such a video comes in. English stays on the bundled base model.
BETTER_MODEL = "small"
# Hindi and Urdu sound the same; Whisper often labels Hindi speech "ur" and then writes Urdu script.
# Our streamers are mostly Indian, so that becomes Hindi (Devanagari). "ur" chosen by hand stays.
_SAME_SPEECH = {"ur": "hi"}


# A Hindi hint written in Hindi letters keeps Whisper writing Hindi (Devanagari, later Hinglish)
# instead of translating mixed Hindi-English speech into English.
HINDI_PROMPT = "यह हिंदी और अंग्रेज़ी में गेमिंग वीडियो है। GTA, गेमप्ले, भाई, क्या, चल, यार।"


def _detect_language(model, samples) -> tuple[str | None, float]:
    """Whisper's language guess. Hinglish (Hindi mixed with English words) is often guessed as
    English; if Hindi/Urdu got a real share of the vote, it's Hindi."""
    try:
        lang, prob, ranked = model.detect_language(audio=samples, vad_filter=True, language_detection_segments=4)
        votes = dict(ranked or [])
        hindi = float(votes.get("hi", 0)) + float(votes.get("ur", 0))
        if lang not in ("hi", "ur") and hindi >= 0.15:
            return "hi", hindi
        return lang, float(prob)
    except Exception:
        return None, 0.0


def quick_language(audio_path, seconds: float = 45.0) -> str | None:
    """Spoken language of the first speech in a wav (base model, a few seconds of work)."""
    import wave

    import numpy as np

    try:
        with wave.open(str(audio_path), "rb") as wav:
            rate = wav.getframerate()
            wav.setpos(min(wav.getnframes() - 1, int(rate * 20)) if wav.getnframes() > rate * 80 else 0)
            raw = wav.readframes(int(rate * seconds))
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        with _MODEL_CACHE_LOCK:
            model = _load_model("base")
        lang, _ = _detect_language(model, samples)
        return _SAME_SPEECH.get(lang, lang) if lang else None
    except Exception:
        return None


def transcribe(
    audio_path: Path,
    model_size: str = "base",
    language: str | None = None,
    progress: Callable[[float, float], None] | None = None,
    cancel_event=None,
    task: str = "transcribe",
    status: Callable[[str], None] | None = None,
    info: dict | None = None,
) -> list[dict]:
    """Transcribe audio locally with faster-whisper on CPU (int8).

    ``language=None`` lets Whisper identify the language once, from the first minutes of speech,
    and then keeps it for the whole video (per-chunk guessing flipped between languages).
    A non-English language moves to the better "small" model when it can be loaded.
    ``task="translate"`` writes English captions for any spoken language.
    Progress is reported as ``(processed_seconds, total_seconds)`` after each segment;
    ``status`` gets short messages for the buyer; ``info`` is filled with
    ``{"language", "probability", "model"}``.
    """
    _check_cancelled(cancel_event)
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise TranscriptError(f"Audio file not found: {audio_path}")
    if model_size not in {"tiny", "base", "small", "medium", "large-v3"}:
        raise TranscriptError(
            "Unsupported model size. Choose tiny, base, small, medium, or large-v3."
        )
    if task not in ("transcribe", "translate"):
        raise TranscriptError("task must be transcribe or translate")
    info = info if info is not None else {}
    detected = language is not None  # chosen by hand: kept as it is
    acquired = False
    try:
        while True:
            acquired = _MODEL_LOCK.acquire(timeout=0.1)
            if acquired:
                break
            _check_cancelled(cancel_event)
        _check_cancelled(cancel_event)
        try:
            with _MODEL_CACHE_LOCK:
                model = _load_model(model_size)
        except ImportError as exc:
            raise TranscriptError(
                "Local transcription requires faster-whisper. Install faster-whisper to continue."
            ) from exc
        results = []
        with tempfile.TemporaryDirectory(prefix="clipstudio-transcribe-") as temp_dir:
            wav_path = Path(temp_dir) / "audio.wav"
            extract_audio(
                audio_path,
                wav_path,
                sample_rate=_SAMPLE_RATE,
                cancel_event=cancel_event,
            )
            with wave.open(str(wav_path), "rb") as wav:
                total = wav.getnframes() / wav.getframerate()
                chunk_frames = _CHUNK_SECONDS * wav.getframerate()
                processed = 0.0
                while True:
                    _check_cancelled(cancel_event)
                    samples_raw = wav.readframes(chunk_frames)
                    if not samples_raw:
                        break
                    samples = (
                        np.frombuffer(samples_raw, dtype="<i2").astype(np.float32)
                        / 32768.0
                    )
                    chunk_start = processed
                    if not detected:
                        detected = True
                        guess, probability = _detect_language(model, samples)
                        if guess:
                            language = _SAME_SPEECH.get(guess, guess)
                            info.update(language=language, probability=round(probability, 2))
                    if language and language != "en" and model_size in ("tiny", "base") and info.get("model") is None:
                        info["model"] = model_size
                        if status:
                            status(f"{LANGUAGE_NAMES.get(language, language)} speech: switching to the better speech "
                                   "model (downloads once, about 480 MB)")
                        try:
                            with _MODEL_CACHE_LOCK:
                                model = _load_model(BETTER_MODEL)
                            info["model"] = BETTER_MODEL
                        except Exception:
                            pass  # offline or no space: the base model still writes captions
                    # The base model is less sure of itself outside English: keep more of what it heard.
                    strict = language in (None, "en")
                    segments, _ = model.transcribe(
                        samples,
                        language=language,
                        task=task,
                        **({"initial_prompt": HINDI_PROMPT} if language == "hi" and task == "transcribe" else {}),
                        beam_size=5,
                        vad_filter=True,
                        condition_on_previous_text=False,
                        word_timestamps=True,
                    )
                    chunk_end = chunk_start + len(samples) / wav.getframerate()

                    chunk_progress = chunk_start

                    def report_chunk_progress(value):
                        nonlocal chunk_progress
                        chunk_progress = max(chunk_progress, min(total, value))
                        if progress:
                            progress(chunk_progress, total)

                    for segment in segments:
                        _check_cancelled(cancel_event)
                        try:
                            segment_end = float(segment.end)
                        except (AttributeError, TypeError, ValueError):
                            segment_end = 0.0
                        report_chunk_progress(
                            min(chunk_start + max(0.0, segment_end), chunk_end)
                        )
                        text = str(getattr(segment, "text", "")).strip()
                        if not text:
                            continue
                        try:
                            avg_logprob = float(getattr(segment, "avg_logprob", 0) or 0)
                            no_speech_prob = float(getattr(segment, "no_speech_prob", 0) or 0)
                            compression_ratio = float(getattr(segment, "compression_ratio", 0) or 0)
                        except (TypeError, ValueError):
                            continue
                        if (avg_logprob < (-1.0 if strict else -1.5)
                                or no_speech_prob > (0.6 if strict else 0.8)
                                or compression_ratio > 2.4):
                            continue
                        start = float(segment.start) + chunk_start
                        end = float(segment.end) + chunk_start
                        word_times = []
                        for word in getattr(segment, "words", None) or ():
                            try:
                                word_start = float(word.start) + chunk_start
                                word_end = float(word.end) + chunk_start
                            except (AttributeError, TypeError, ValueError):
                                continue
                            if word_end > word_start:
                                word_times.append((word_start, word_end))
                        if word_times:
                            clean_words = []
                            for word in getattr(segment, "words", None) or ():
                                try:
                                    word_start = max(
                                        chunk_start, float(word.start) + chunk_start
                                    )
                                    word_end = min(
                                        chunk_end, float(word.end) + chunk_start
                                    )
                                    word_text = str(word.word).strip()
                                except (AttributeError, TypeError, ValueError):
                                    continue
                                if word_end <= word_start or not word_text:
                                    continue
                                raw_probability = getattr(word, "probability", None)
                                item = {
                                    "start": word_start,
                                    "end": word_end,
                                    "text": word_text,
                                }
                                if raw_probability is not None:
                                    try:
                                        probability = float(raw_probability)
                                    except (TypeError, ValueError):
                                        continue
                                    if not math.isfinite(probability) or not 0 <= probability <= 1:
                                        continue
                                    item["probability"] = probability
                                clean_words.append(item)
                            word_times = [(w["start"], w["end"]) for w in clean_words]
                            if clean_words:
                                start = min(bound[0] for bound in word_times)
                                end = max(bound[1] for bound in word_times)
                                text = " ".join(w["text"] for w in clean_words)
                                words = clean_words
                            else:
                                words = []
                        else:
                            words = []
                        start = min(start, chunk_end)
                        end = min(end, chunk_end)
                        if end <= start:
                            continue
                        if len(text.split()) <= 3 and end - start > 8.0:
                            continue
                        raw_item = {"start": start, "end": end, "text": text}
                        if words:
                            raw_item["words"] = words
                        item = _clean_segment(raw_item)
                        results.append(item)
                    processed += len(samples) / wav.getframerate()
                    report_chunk_progress(chunk_end)
        _check_cancelled(cancel_event)
    finally:
        if acquired:
            _MODEL_LOCK.release()
    info.setdefault("language", language)
    info.setdefault("model", model_size)
    return results


def _clip_bounds(clip) -> tuple[float, float]:
    try:
        start = clip["start"] if isinstance(clip, dict) else clip.start
        end = clip["end"] if isinstance(clip, dict) else clip.end
        start, end = float(start), float(end)
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise TranscriptError("Each clip needs start and end times.") from exc
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise TranscriptError(
            "Clip end must be greater than its finite, nonnegative start."
        )
    return start, end


def map_transcript_to_clips(
    segments: Iterable[dict], clips: Iterable
) -> list[list[dict]]:
    """Clip source cues into per-clip local timelines (each begins at zero)."""
    clean = [_clean_segment(item) for item in segments]
    mapped = []
    for clip in clips:
        clip_start, clip_end = _clip_bounds(clip)
        cues = []
        for segment in clean:
            if segment.get("words"):
                words = []
                for word in segment["words"]:
                    start = max(clip_start, word["start"])
                    end = min(clip_end, word["end"])
                    if end > start:
                        words.append(
                            {
                                **word,
                                "start": start - clip_start,
                                "end": end - clip_start,
                            }
                        )
                if words:
                    cues.append(
                        {
                            "start": words[0]["start"],
                            "end": words[-1]["end"],
                            "text": " ".join(word["text"] for word in words),
                            "words": words,
                        }
                    )
                continue
            start, end = (
                max(clip_start, segment["start"]),
                min(clip_end, segment["end"]),
            )
            if end > start:
                cues.append(
                    {
                        "start": start - clip_start,
                        "end": end - clip_start,
                        "text": segment["text"],
                    }
                )
        mapped.append(cues)
    return mapped


def map_transcript_to_concatenation(
    segments: Iterable[dict], clips: Iterable
) -> list[dict]:
    """Map source cues onto the concatenated output timeline in clip order."""
    clips = list(clips)
    per_clip = map_transcript_to_clips(segments, clips)
    offset, result = 0.0, []
    for clip, cues in zip(clips, per_clip):
        clip_start, clip_end = _clip_bounds(clip)
        for cue in cues:
            shifted = {
                "start": cue["start"] + offset,
                "end": cue["end"] + offset,
                "text": cue["text"],
            }
            if cue.get("words"):
                shifted["words"] = [
                    {
                        **word,
                        "start": word["start"] + offset,
                        "end": word["end"] + offset,
                    }
                    for word in cue["words"]
                ]
            result.append(shifted)
        offset += clip_end - clip_start
    return result


def _format_timestamp(seconds: float) -> str:
    millis_total = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def segments_to_srt(segments: Iterable[dict]) -> str:
    """Serialize validated segments as UTF-8-ready SubRip text."""
    cues = [_clean_segment(item) for item in segments]
    return "".join(
        f"{index}\n{_format_timestamp(cue['start'])} --> {_format_timestamp(cue['end'])}\n"
        f"{cue['text'].replace(chr(10), ' ').replace(chr(13), ' ')}\n\n"
        for index, cue in enumerate(cues, 1)
    ).rstrip() + ("\n" if cues else "")


def write_clip_srt(
    segments: Iterable[dict], clip, output_path: Path | None = None
) -> str:
    """Build one short's captions, optionally writing them to ``output_path``."""
    cues = map_transcript_to_clips(segments, [clip])[0]
    rendered = segments_to_srt(cues)
    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    return rendered
