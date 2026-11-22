import json

import pytest

from clipstudio.engine import Candidate
from clipstudio.director import parse_director_response, rerank_candidates


def test_parser_reranks_valid_unique_ids_without_changing_windows():
    candidates = [Candidate(10, 20, 1.0, ["loud"]), Candidate(30, 40, 2.0, ["motion"])]
    result = parse_director_response(
        {
            "moments": [
                {"id": "1", "score": 9, "reason": "joke payoff", "title": "No way"},
                {"id": "0", "score": 4, "reason": "setup", "title": "Wanted level"},
            ]
        },
        candidates,
    )
    assert [(c.start, c.end, c.score) for c in result] == [(30, 40, 9), (10, 20, 4)]
    assert "Director title: No way" in result[0].reasons


@pytest.mark.parametrize(
    "moments",
    [
        [{"id": "made-up", "score": 9, "reason": "x", "title": "x"}],
        [{"id": "0", "score": float("nan"), "reason": "x", "title": "x"}],
        [
            {"id": "0", "score": 4, "reason": "x", "title": "x"},
            {"id": "0", "score": 5, "reason": "y", "title": "y"},
        ],
        [
            {"id": "0", "score": 8, "reason": "valid", "title": "Title"},
            {"id": "1", "score": 7, "reason": "bad field case", "Title": "Wrong case"},
        ],
        [{"id": "0", "score": 8, "reason": "incomplete", "title": "Only one of two"}],
    ],
)
def test_parser_rejects_fabricated_duplicate_incomplete_or_nonfinite_scores(moments):
    with pytest.raises(ValueError):
        parse_director_response(
            {"moments": moments}, [Candidate(1, 2, 1), Candidate(3, 4, 2)]
        )


def test_parser_rejects_captured_phi4_omission_and_title_case_failure():
    candidates = [Candidate(i * 30, i * 30 + 20, float(i)) for i in range(8)]
    captured = {
        "moments": [
            {
                "id": "0",
                "score": 8,
                "reason": "Exciting chase with humorous reference.",
                "title": "Chase Avoiding Cops",
            },
            {
                "id": "1",
                "score": 7,
                "reason": "Character humorously asking for permission.",
                "Title": "Planning Robbery",
            },
        ]
    }
    with pytest.raises(ValueError):
        parse_director_response(captured, candidates)


def test_no_configured_model_returns_candidates_without_network(monkeypatch):
    monkeypatch.delenv("CLIPSTUDIO_DIRECTOR_URL", raising=False)
    monkeypatch.delenv("CLIPSTUDIO_DIRECTOR_MODEL", raising=False)
    original = [Candidate(2, 6, 3, ["audio"])]
    ranked, warnings = rerank_candidates(original, "a funny clip")
    assert ranked == original
    assert warnings


def test_configured_model_reranks_top_candidates_in_batches_and_preserves_windows(
    monkeypatch,
):
    import clipstudio.director as director

    monkeypatch.setenv("CLIPSTUDIO_DIRECTOR_MODEL", "test-model")
    monkeypatch.delenv("CLIPSTUDIO_DIRECTOR_URL", raising=False)
    calls = []

    def fake_request(url, model, prompt, key, compatible, candidate_ids):
        calls.append((url, model, key, compatible, candidate_ids))
        rows = json.loads(prompt.split("\n", 1)[1])["moments"]
        return {
            "moments": [
                {
                    "id": row["id"],
                    "score": 8,
                    "reason": "setup and payoff",
                    "title": "Great escape",
                }
                for row in rows
            ]
        }

    monkeypatch.setattr(director, "_request", fake_request)
    candidates = [
        Candidate(i * 3, i * 3 + 2, float(i), ["audio clue"]) for i in range(45)
    ]
    transcript = [{"start": 0, "end": 2, "text": "arre kya hua"}]
    ranked, warnings = rerank_candidates(candidates, transcript)
    assert len(calls) == 9
    assert all(len(call[-1]) <= 5 for call in calls)
    assert not warnings
    assert len(ranked) == len(candidates)
    assert all(
        any(reason.startswith("Director title:") for reason in c.reasons)
        for c in ranked[:20]
    )
    assert all(
        (c.start, c.end) in {(x.start, x.end) for x in candidates} for c in ranked
    )


def test_bad_model_output_falls_back_to_original_candidates(monkeypatch):
    import clipstudio.director as director

    monkeypatch.setenv("CLIPSTUDIO_DIRECTOR_MODEL", "test-model")
    monkeypatch.setattr(
        director,
        "_request",
        lambda *args: {
            "moments": [{"id": "fabricated", "score": 10, "reason": "x", "title": "x"}]
        },
    )
    candidates = [Candidate(1, 5, 2.0, ["audio"])]
    ranked, warnings = rerank_candidates(candidates, "test")
    assert ranked is candidates
    assert warnings
