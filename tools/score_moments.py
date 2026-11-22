"""Score the moment picker on the test set (moment_eval/cases), before vs after.

python tools/score_moments.py [--write] [--signals]   (--write saves moment_eval/RESULTS.md; --signals = per-signal table only)

Each case is 10 real minutes of a GTA stream; the good moments are the clips Twitch viewers made.
Both pickers get the same saved numbers (loudness, on-screen change, transcript) and make five
30-second Shorts, best first.
  payoff     a Twitch clip records ~25 s before the viewer presses Clip and ~5 s after, so the
             moment that made them press sits 13..4 s before the clip's end
  hit        a clip that keeps >= 5 s of that payoff and >= half of the moment (or 12 s of it)
  P@3        how many of the first 3 clips are hits
  R@5        how many marked moments the 5 clips found
  clean cuts clip starts/ends that are not in the middle of a sentence
  setup/react median seconds kept before / after the payoff (hits only)
  score      100 x (0.35 P@3 + 0.45 R@5 + 0.20 clean cuts)
  rank AUC   uses every second, not just 5 picks: the chance that a payoff second scores higher
             than a second outside every marked moment (0.5 = no better than a coin)
"Tune" cases were looked at while setting the rules; "held back" cases were not.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipstudio.engine import rank_moments  # noqa: E402
import numpy as np  # noqa: E402

from clipstudio.moments import cuts_mid_sentence, sentences, timeline  # noqa: E402
from clipstudio.shorts import select_shorts  # noqa: E402
from moment_eval import baseline_v1  # noqa: E402

CASES = ROOT / "moment_eval" / "cases"
SPLIT = json.loads((ROOT / "moment_eval" / "split.json").read_text(encoding="utf-8"))
LENGTH, COUNT = 30, 5
USE_CHAT = "--no-chat" not in sys.argv


def new_pick(case):
    cands = rank_moments(case["duration"], case["transcript"], case["rms"], case["peak"], case["motion2"], case.get("heat"),
                         chat=case.get("chat") if USE_CHAT else None)
    return select_shorts(cands, case["transcript"], case["duration"], length=LENGTH, count=COUNT)


def old_pick(case):
    return baseline_v1.pick(case, LENGTH, COUNT)


def random_picks(case, rng):
    out = []
    while len(out) < COUNT:
        s = rng.uniform(0, case["duration"] - LENGTH)
        if not any(s < o[1] and s + LENGTH > o[0] for o in out):
            out.append((s, s + LENGTH))
    return out


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def payoff(m):
    return max(m["start"], m["end"] - 13), m["end"] - 4


def is_hit(clip, m):
    s, e = clip
    a, b = payoff(m)
    need = min(0.5 * (m["end"] - m["start"]), 12)
    return overlap(s, e, a, b) >= min(5, b - a) and overlap(s, e, m["start"], m["end"]) >= need


def rank_auc(case, scores):
    scores = np.asarray(scores, dtype=float)
    n = len(scores)
    pay, marked = np.zeros(n, bool), np.zeros(n, bool)
    for m in case["moments"]:
        a, b = payoff(m)
        pay[max(0, int(a)):min(n, int(b))] = True
        marked[max(0, int(m["start"])):min(n, int(m["end"]))] = True
    x, y = scores[pay], scores[~marked]
    if not x.size or not y.size:
        return 0.5
    return float(np.mean(x[:, None] > y[None, :]) + 0.5 * np.mean(x[:, None] == y[None, :]))


def measure(case, clips):
    sents = sentences(case["transcript"])
    moments = case["moments"]
    hits3 = sum(1 for c in clips[:3] if any(is_hit(c, m) for m in moments))
    found = [m for m in moments if any(is_hit(c, m) for c in clips[:5])]
    cuts = [t for c in clips for t in c]
    clean = sum(1 for t in cuts if not cuts_mid_sentence(t, sents)) / max(1, len(cuts))
    setup, react = [], []
    for m in found:
        c = next(c for c in clips if is_hit(c, m))
        a, b = payoff(m)
        setup.append(a - c[0])
        react.append(c[1] - b)
    return {"p3": hits3 / 3, "r5": len(found) / max(1, len(moments)), "clean": clean, "setup": setup, "react": react, "auc": None}


def total(rows):
    p3 = statistics.mean(r["p3"] for r in rows)
    r5 = statistics.mean(r["r5"] for r in rows)
    clean = statistics.mean(r["clean"] for r in rows)
    setup = [x for r in rows for x in r["setup"]]
    react = [x for r in rows for x in r["react"]]
    return {
        "P@3": round(p3, 3), "R@5": round(r5, 3), "clean cuts": round(clean, 3),
        "setup s": round(statistics.median(setup), 1) if setup else None,
        "react s": round(statistics.median(react), 1) if react else None,
        "score": round(100 * (0.35 * p3 + 0.45 * r5 + 0.2 * clean), 1),
        "auc": round(statistics.mean(r["auc"] for r in rows), 3) if all(r["auc"] is not None for r in rows) else None,
    }


def signal_table(cases):
    """--signals: rank AUC of every signal on its own (0.5 = coin). Includes the extra signals other
    clippers use (raised voice, loudness vs a 2-minute baseline, police-light strobe, flash) when
    tools/add_case_signals.py has measured them."""
    from clipstudio.moments import _smooth

    def local_z(x, win=120):
        x = np.asarray(x, float)
        k = np.ones(win) / win
        mu = np.convolve(x, k, "same")
        sd = np.sqrt(np.maximum(np.convolve(x * x, k, "same") - mu * mu, 0)) + 1e-6
        return np.clip((x - mu) / sd / 3.5, 0, 1)

    rows = {}
    for c in cases:
        n = len(c["rms"])
        tl = timeline(c["duration"], c["transcript"], c["rms"], c["peak"], c["motion2"], c.get("heat"), c.get("chat"))
        sig = {k: tl.parts[k][:n] for k in ("event", "talk", "jump", "loud", "scene", "chat")}
        if "voice" in c:
            rms = np.asarray(c["rms"])
            sig["voice"] = _smooth(np.asarray(c["voice"][:n]), 3)
            sig["shout vs 2 min"] = _smooth(local_z(np.asarray(c["voice"][:n]) * rms), 3)
            sig["loud vs 2 min"] = _smooth(local_z(rms), 3)
            sig["police lights"] = np.convolve(c["strobe"][:n], np.ones(9), "same")
            sig["flash"] = np.convolve(c["flash"][:n], np.ones(9), "same")
        sig["picker score"] = tl.score[:n]
        rows[c["id"]] = {k: rank_auc(c, v) for k, v in sig.items()}
    keys = list(dict.fromkeys(k for r in rows.values() for k in r))
    out = ["| signal alone | tune | held back | all |", "|---|---|---|---|"]
    for k in keys:
        vals = {g: [rows[i][k] for i in SPLIT[g] if i in rows and k in rows[i]] for g in ("tune", "held_back")}
        every = [r[k] for r in rows.values() if k in r]
        out.append(f"| {k} | {statistics.mean(vals['tune']):.2f} | {statistics.mean(vals['held_back']):.2f} | {statistics.mean(every):.2f} |"
                   if vals["tune"] and vals["held_back"] else f"| {k} | - | - | {statistics.mean(every):.2f} |")
    return "\n".join(out)


def main():
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CASES.glob("*.json"))]
    if "--signals" in sys.argv:
        print(signal_table(cases))
        return {}
    if "--tune-only" in sys.argv:  # while changing rules: never peek at the held-back cases
        cases = [c for c in cases if c["id"] in SPLIT["tune"]]
    rng = random.Random(7)
    per = {"old": {}, "new": {}, "random": {}}
    lines = ["| case | game | moments | old P@3 / R@5 / clean / AUC | new P@3 / R@5 / clean / AUC |", "|---|---|---|---|---|"]
    for case in cases:
        old = measure(case, [(c.start, c.end) for c in old_pick(case)])
        new = measure(case, [(c.start, c.end) for c in new_pick(case)])
        old["auc"] = rank_auc(case, baseline_v1.second_scores(case))
        new["auc"] = rank_auc(case, timeline(case["duration"], case["transcript"], case["rms"], case["peak"],
                                             case["motion2"], case.get("heat"), case.get("chat") if USE_CHAT else None).score)
        rand = [measure(case, random_picks(case, rng)) for _ in range(200)]
        per["old"][case["id"]], per["new"][case["id"]] = old, new
        per["random"][case["id"]] = {
            "p3": statistics.mean(r["p3"] for r in rand), "r5": statistics.mean(r["r5"] for r in rand),
            "clean": statistics.mean(r["clean"] for r in rand), "setup": [], "react": [], "auc": 0.5,
        }
        lines.append(f"| {case['id']} | {case['source']['game']} | {len(case['moments'])} | "
                     f"{old['p3']:.2f} / {old['r5']:.2f} / {old['clean']:.2f} / {old['auc']:.2f} | "
                     f"{new['p3']:.2f} / {new['r5']:.2f} / {new['clean']:.2f} / {new['auc']:.2f} |")
    summary = ["| picker | cases | P@3 | R@5 | clean cuts | setup s | react s | rank AUC | score |", "|---|---|---|---|---|---|---|---|---|"]
    out = {}
    for name in ("random", "old", "new"):
        for group, ids in (("all", [c["id"] for c in cases]), ("tune", SPLIT["tune"]), ("held back", SPLIT["held_back"])):
            rows = [per[name][i] for i in ids if i in per[name]]
            if not rows:
                continue
            t = total(rows)
            out[f"{name}/{group}"] = t
            summary.append(f"| {name} | {group} ({len(rows)}) | {t['P@3']:.2f} | {t['R@5']:.2f} | {t['clean cuts']:.2f} | "
                           f"{t['setup s'] if t['setup s'] is not None else '-'} | {t['react s'] if t['react s'] is not None else '-'} | {t['auc']:.2f} | **{t['score']}** |")
    # Is new - old more than luck? Resample the cases 2000 times; a range that crosses 0 is not proof.
    ids = [c["id"] for c in cases]
    boot = random.Random(11)
    spread = ["", "| new - old (all cases) | difference | 90% range when cases are resampled |", "|---|---|---|"]
    for key, label in (("score", "score"), ("auc", "rank AUC"), ("clean", "clean cuts"), ("r5", "R@5"), ("p3", "P@3")):
        def value(r, key=key):
            return 100 * (0.35 * r["p3"] + 0.45 * r["r5"] + 0.2 * r["clean"]) if key == "score" else r[key]

        def diff(sample):
            return statistics.mean(value(per["new"][i]) - value(per["old"][i]) for i in sample)

        draws = sorted(diff([boot.choice(ids) for _ in ids]) for _ in range(2000))
        spread.append(f"| {label} | {diff(ids):+.2f} | {draws[100]:+.2f} .. {draws[1899]:+.2f} |")
    text = ("\n".join(summary) + "\n" + "\n".join(spread) + "\n\n" + "\n".join(lines)
            + "\n\n### Each signal on its own (rank AUC, 0.5 = coin)\n\n" + signal_table(cases))
    print(text)
    if "--write" in sys.argv:
        head = (ROOT / "tools" / "score_moments.py").read_text(encoding="utf-8").split('"""')[1].strip()
        (ROOT / "moment_eval" / "RESULTS.md").write_text(
            "# Moment picker: before vs after\n\nMade by `python tools/score_moments.py --write`.\n\n```\n" + head + "\n```\n\n" + text + "\n",
            encoding="utf-8")
    return out


if __name__ == "__main__":
    main()
