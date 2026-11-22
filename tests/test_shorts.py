import itertools

import pytest

from clipstudio.engine import Candidate
from clipstudio.shorts import select_shorts


def test_dialogue_hook_starts_before_first_speech_and_keeps_payoff():
    candidate = Candidate(664, 694, 9, ["high audio energy"])
    transcript = [
        {"start": 678, "end": 681, "text": "Wait, watch this."},
        {"start": 684, "end": 686, "text": "Oh no!"},
        {"start": 690, "end": 692, "text": "I crashed."},
    ]
    (short,) = select_shorts([candidate], transcript, duration=800, length=30, count=1)
    assert short.start == pytest.approx(676.5)
    assert short.end == pytest.approx(706.5)
    assert short.end >= transcript[-1]["end"]
    assert short.score == candidate.score
    assert "speech lead-in" in short.reasons


def test_no_transcript_speech_uses_centered_candidate_window():
    (short,) = select_shorts(
        [Candidate(100, 130, 5)], [], duration=200, length=20, count=1
    )
    assert (short.start, short.end) == pytest.approx((105, 125))
    assert "speech lead-in" not in short.reasons


def test_empty_transcript_text_is_not_treated_as_speech():
    (short,) = select_shorts(
        [Candidate(100, 130, 5)],
        [{"start": 100, "end": 105, "text": "  "}],
        duration=200,
        length=20,
        count=1,
    )
    assert (short.start, short.end) == pytest.approx((105, 125))
    assert "speech lead-in" not in short.reasons


def test_speech_window_respects_source_bounds_and_short_sources():
    (end_short,) = select_shorts(
        [Candidate(75, 95, 4)],
        [{"start": 90, "end": 92, "text": "Last second"}],
        duration=100,
        length=30,
        count=1,
    )
    assert (end_short.start, end_short.end) == pytest.approx((70, 100))
    (small,) = select_shorts([Candidate(0, 12, 4)], [], duration=12, length=30, count=1)
    assert (small.start, small.end) == pytest.approx((0, 12))


def test_speech_payoff_near_window_end_shifts_clip_to_keep_last_line():
    transcript = [
        {"start": 100, "end": 100.5, "text": "setup"},
        {"start": 129, "end": 130, "text": "payoff"},
    ]
    (short,) = select_shorts(
        [Candidate(100, 140, 7)], transcript, duration=200, length=30, count=1
    )
    assert short.start == pytest.approx(100)
    assert short.end == pytest.approx(130)
    assert short.end >= transcript[-1]["end"]


def test_selection_preserves_rank_and_skips_overlapping_retimed_windows():
    candidates = [
        Candidate(20, 50, 10, []),
        Candidate(40, 70, 9, []),
        Candidate(100, 130, 8, []),
    ]
    transcript = [{"start": 30, "end": 32, "text": "speech"}]
    selected = select_shorts(candidates, transcript, duration=200, length=30, count=3)
    assert [c.score for c in selected] == [10, 8]
    ordered = sorted(selected, key=lambda c: c.start)
    assert all(a.end <= b.start for a, b in itertools.pairwise(ordered))


def test_count_and_invalid_length_are_handled():
    assert select_shorts([Candidate(0, 10, 1)], [], duration=10, count=0) == []
    with pytest.raises(ValueError):
        select_shorts([], [], duration=10, length=0)
