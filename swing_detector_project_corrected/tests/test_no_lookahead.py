"""No-lookahead and non-repainting tests (Phase 2 exit criterion: 100% pass).

Four independent properties are checked, on several series and fractal sizes:

  1. Truncation: at hundreds of random cut points the output on history[:cut] equals,
     exactly and in order, the full-history output restricted to swings confirmed by the cut.
  2. Perturbation: replacing every candle after a cut with different data leaves all
     previously confirmed events byte-identical.
  3. Timing/oracle: events equal an independent brute-force fractal, confirmed on the
     close of candle pivot+n.
  4. Validity: every emitted swing is still a genuine fractal given the complete history
     (a swing later invalidated by new data would be a repaint).

The mutation tests prove these checks can fail: four deliberately defective detectors are
run through them, and through a re-implementation of the original (weaker) test.
"""

from __future__ import annotations

import random

import pytest

import mutants
from checks import random_cuts, timing_check, truncation_check, validity_check, weak_original_check
from helpers import dicts, walk
from trading_system.data.ohlc import Candle, OHLCSeries
from trading_system.detectors.swing import SwingDetector
from trading_system.replay import replay_series

CASES = [(2, 2026, "M5"), (3, 7, "H1"), (2, 99, "M3"), (1, 5, "M15"), (4, 31, "M5")]


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_truncation_at_hundreds_of_random_cut_points(n, seed, tf):
    series = walk(600, seed, "V75", tf)
    cuts = random_cuts(len(series), 300, seed=seed + 1)
    assert len(cuts) >= 300
    assert truncation_check(mutants.correct(n), series, cuts) == []


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_perturbing_future_candles_never_changes_confirmed_events(n, seed, tf):
    a, b = walk(400, seed, "V75", tf), walk(400, seed + 1000, "V75", tf)
    full_a = replay_series(a, SwingDetector("V75", tf, {"n": n}))
    for cut in random_cuts(400, 100, seed):
        spliced = list(a)[:cut] + [Candle(c.time, c.open, c.high, c.low, c.close, c.volume, c.index)
                                   for c in list(b)[cut:]]
        ev = replay_series(OHLCSeries(spliced, "V75", tf), SwingDetector("V75", tf, {"n": n}))
        last_bar = cut - 1
        known = [e for e in ev if e.payload["confirmation_bar_index"] <= last_bar]
        assert dicts(known) == dicts([e for e in full_a if e.payload["confirmation_bar_index"] <= last_bar])


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_timing_against_independent_oracle(n, seed, tf):
    assert timing_check(mutants.correct(n), walk(500, seed, "V75", tf), n) == []


@pytest.mark.parametrize("n,seed,tf", CASES)
def test_every_confirmed_swing_remains_a_valid_fractal_with_full_history(n, seed, tf):
    assert validity_check(mutants.correct(n), walk(500, seed, "V75", tf), n) == []


# ------------------------------------------------------------------ mutation analysis
MUTANTS = {
    "early_confirm_repaints": mutants.early_confirm_repaints,
    "one_bar_late": mutants.one_bar_late,
    "leaky_tail_batch": mutants.leaky_tail_batch,
    "open_time_confirmation": mutants.open_time_confirmation,
}


def _strong_failures(run, series, n, cuts):
    return (truncation_check(run, series, cuts) + timing_check(run, series, n)
            + validity_check(run, series, n))


@pytest.mark.parametrize("name", MUTANTS)
def test_strong_suite_detects_every_mutant(name):
    n = 2
    series = walk(500, 2026, "V75", "M5")
    cuts = random_cuts(len(series), 200, seed=1)
    assert _strong_failures(mutants.correct(n), series, n, cuts) == []  # control: real detector passes
    assert _strong_failures(MUTANTS[name](n), series, n, cuts), f"mutant {name} was NOT detected"


def test_original_weak_test_misses_defects_the_strong_suite_catches():
    """Evidence for audit finding: the original test only checked 'prefix events are a subset of
    full events', so consistent-but-wrong detectors pass it."""
    n, series = 2, walk(500, 2026, "V75", "M5")
    cuts = random_cuts(len(series), 200, seed=1)
    missed = [name for name, mk in MUTANTS.items()
              if not weak_original_check(mk(n), series, cuts)
              and _strong_failures(mk(n), series, n, cuts)]
    assert len(missed) >= 3, f"weak test unexpectedly caught most mutants; missed only {missed}"
    # the real detector passes the weak test too, so it is necessary but not sufficient
    assert weak_original_check(mutants.correct(n), series, cuts) == []
