# Moment picker: before vs after

Made by `python tools/score_moments.py --write`.

```
Score the moment picker on the test set (moment_eval/cases), before vs after.

python tools/score_moments.py [--write]      (--write saves moment_eval/RESULTS.md)

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
```

| picker | cases | P@3 | R@5 | clean cuts | setup s | react s | rank AUC | score |
|---|---|---|---|---|---|---|---|---|
| random | all (8) | 0.16 | 0.22 | 0.75 | - | - | 0.50 | **30.4** |
| random | tune (4) | 0.18 | 0.23 | 0.77 | - | - | 0.50 | **31.7** |
| random | held back (4) | 0.14 | 0.21 | 0.74 | - | - | 0.50 | **29.2** |
| old | all (8) | 0.29 | 0.28 | 0.82 | 6.4 | 14.6 | 0.47 | **39.1** |
| old | tune (4) | 0.25 | 0.20 | 0.88 | 4.0 | 17.0 | 0.49 | **35.1** |
| old | held back (4) | 0.33 | 0.35 | 0.78 | 10.3 | 10.7 | 0.46 | **43.1** |
| new | all (8) | 0.21 | 0.33 | 1.00 | 13.2 | 11.3 | 0.56 | **42.2** |
| new | tune (4) | 0.25 | 0.33 | 1.00 | 10.0 | 11.8 | 0.56 | **43.6** |
| new | held back (4) | 0.17 | 0.33 | 1.00 | 16.8 | 6.5 | 0.55 | **40.8** |

| new - old (all cases) | difference | 90% range when cases are resampled |
|---|---|---|
| score | +3.11 | -5.59 .. +11.23 |
| rank AUC | +0.08 | -0.02 .. +0.18 |
| clean cuts | +0.17 | +0.09 .. +0.26 |
| R@5 | +0.06 | -0.06 .. +0.17 |
| P@3 | -0.08 | -0.21 .. +0.04 |

| case | game | moments | old P@3 / R@5 / clean / AUC | new P@3 / R@5 / clean / AUC |
|---|---|---|---|---|
| bubbell-gta5rp-3309 | Grand Theft Auto V | 3 | 0.33 / 0.33 / 1.00 / 0.47 | 0.33 / 0.33 / 1.00 / 0.71 |
| carmen-gta6-8373 | Grand Theft Auto VI | 3 | 0.33 / 0.33 / 0.80 / 0.39 | 0.33 / 0.33 / 1.00 / 0.60 |
| chatterbox-gta5rp-3449 | Grand Theft Auto V | 4 | 0.33 / 0.25 / 0.60 / 0.59 | 0.00 / 0.25 / 1.00 / 0.57 |
| chatterbox-gta5rp-4897 | Grand Theft Auto V | 4 | 0.33 / 0.25 / 0.60 / 0.64 | 0.00 / 0.25 / 1.00 / 0.54 |
| darkviperau-gta6-5714 | Grand Theft Auto VI | 5 | 0.33 / 0.20 / 0.90 / 0.42 | 0.33 / 0.40 / 1.00 / 0.50 |
| nakkida-gta5rp-7183 | Grand Theft Auto V | 3 | 0.33 / 0.33 / 0.90 / 0.54 | 0.00 / 0.00 / 1.00 / 0.42 |
| ster-gta5rp-9706 | Grand Theft Auto V | 4 | 0.33 / 0.50 / 0.80 / 0.25 | 0.33 / 0.75 / 1.00 / 0.66 |
| syndicate-gta6-0379 | Grand Theft Auto VI | 3 | 0.00 / 0.00 / 1.00 / 0.49 | 0.33 / 0.33 / 1.00 / 0.47 |

## In plain words (22 Nov 2026)

- **Proven better: clean cuts.** Every new clip starts and ends between sentences (100% vs 82%);
  the 90% range of the gain is above zero. Clips keep ~10 s of setup and ~11 s of reaction.
- **Not proven: finding better moments.** The new picker finds a few more of the viewers' moments
  (R@5 0.33 vs 0.28) and ranks payoff seconds higher (0.56 vs 0.47; the old score ranked them
  *below* the rest of the stream), but its first 3 picks hit less often (0.21 vs 0.29), and every
  "finding" range crosses zero. Read it as "about the same, with much better cuts".
- **Chat matters.** Without Twitch chat replay (YouTube links and files today) the new picker
  scores lower:

| picker | cases | P@3 | R@5 | clean cuts | setup s | react s | rank AUC | score |
|---|---|---|---|---|---|---|---|---|
| new | all (8) | 0.21 | 0.24 | 1.00 | 10.6 | 11.8 | 0.52 | **38.0** |
| new | tune (4) | 0.33 | 0.33 | 1.00 | 10.6 | 11.8 | 0.58 | **46.5** |
| new | held back (4) | 0.08 | 0.15 | 1.00 | 10.8 | 12.2 | 0.47 | **29.5** |

- Loudness, motion and spoken phrases barely predict what viewers clip in GTA roleplay: those
  moments are jokes and story beats. That is the honest limit of a no-AI picker; AI boost is the
  answer for "funny".
- Metric change, disclosed: the first runs counted a hit as "keeps the clip's last 6 s". Twitch
  clips record ~25 s before the viewer presses Clip and ~5 s after, so the payoff was moved to
  13..4 s before the clip's end. Changed once, for both pickers.
- How blind is "held back"? Not perfectly. Rule details (what-if wording, chat delay, weights) were
  only set on the tune cases, but the held-back totals were printed a few times while the tool was
  being built, and the chat signal was added after one of those runs showed the audio/speech signals
  were weak everywhere. Treat it as a second check, not a clean blind test.

## Other clippers' no-AI signals, tested (22 Nov 2026, later)

NeonClipping (MIT) and Clips Kitty score moments with signals we didn't have: raised voice (share of
sound in the 2-4 kHz band), loudness against a 2-minute baseline, police-light red/blue flicker and
explosion-like flashes. `tools/add_case_signals.py` measured all four on the same 8 stretches and
`python tools/score_moments.py --signals` scores each signal on its own:

| signal alone | tune | held back | all |
|---|---|---|---|
| event (our transcript rules) | 0.49 | 0.52 | 0.50 |
| talk (back-and-forth) | 0.58 | 0.41 | 0.49 |
| jump (loudness jump) | 0.56 | 0.44 | 0.50 |
| loud | 0.53 | 0.42 | 0.48 |
| scene (on-screen change) | 0.50 | 0.62 | 0.56 |
| **chat** | 0.57 | 0.65 | **0.61** |
| voice 2-4 kHz | 0.54 | 0.49 | 0.52 |
| shout vs 2 min | 0.53 | 0.42 | 0.47 |
| loud vs 2 min | 0.52 | 0.42 | 0.47 |
| police lights | 0.52 | 0.50 | 0.51 |
| flash | 0.47 | 0.52 | 0.50 |
| picker score (all mixed) | 0.56 | 0.55 | 0.56 |

- **None of the four new signals beats a coin on these streams**, so they are not switched on.
  Police lights showed up in only 2 of 8 stretches and flashes were rare: roleplay is mostly
  talking. They may matter on action-heavy GTA (chases, stunts); re-test when the set has some.
- **Chat stays the one strong no-AI signal.** Which is why live capture now reads Twitch chat too.
- Open question, not acted on: chat peaks about 14 s before a viewer clip's end, while our payoff
  label sits 13..4 s before the end. Shifting chat 6 s later lifts chat's AUC to 0.71 on both halves,
  but that only lines chat up with our own payoff guess; it would not move real picks much (clips
  already cover the payoff). If more cases confirm it, the payoff label is probably ~5 s too late.
