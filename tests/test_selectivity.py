"""Unit tests for the log-log interpolation and ratio analysis core."""

import math

import pytest

from app.selectivity import Curve, analyze_pair, validate_curve_points


def make_curve(points):
    return Curve.from_points(points)


# --------------------------------------------------------------------------- #
# Curve validation
# --------------------------------------------------------------------------- #
def test_curve_requires_two_points():
    assert validate_curve_points([(100.0, 1.0)]) != []
    assert validate_curve_points([]) != []


def test_curve_requires_positive_currents_and_times():
    assert validate_curve_points([(0.0, 1.0), (100.0, 2.0)]) != []
    assert validate_curve_points([(-5.0, 1.0), (100.0, 2.0)]) != []
    assert validate_curve_points([(100.0, 0.0), (200.0, 2.0)]) != []
    assert validate_curve_points([(100.0, -1.0), (200.0, 2.0)]) != []


def test_curve_requires_strictly_increasing_currents():
    assert validate_curve_points([(100.0, 1.0), (100.0, 2.0)]) != []
    assert validate_curve_points([(200.0, 1.0), (100.0, 2.0)]) != []
    assert validate_curve_points([(100.0, 1.0), (200.0, 2.0), (300.0, 0.5)]) == []


# --------------------------------------------------------------------------- #
# Log-log interpolation
# --------------------------------------------------------------------------- #
def test_log_log_linear_interpolation():
    curve = make_curve([(100.0, 10.0), (400.0, 1.0)])
    # Halfway in log space between (100, 10) and (400, 1):
    # log t = log 10 + (log 1 - log 10) * (log 200 - log 100) / (log 400 - log 100)
    #       = log 10 * (1 - log2/log4) = 0.5 * log 10
    assert curve.time(200.0) == pytest.approx(math.sqrt(10.0), rel=1e-12)
    # Endpoints are reproduced exactly.
    assert curve.time(100.0) == pytest.approx(10.0, rel=1e-12)
    assert curve.time(400.0) == pytest.approx(1.0, rel=1e-12)


def test_multisegment_interpolation():
    curve = make_curve([(50.0, 20.0), (500.0, 1.5), (5000.0, 0.25)])
    assert curve.time(500.0) == pytest.approx(1.5, rel=1e-12)
    # geometric interpolation inside the second segment
    expected = math.exp(
        math.log(1.5)
        + (math.log(0.25) - math.log(1.5))
        * (math.log(1000.0) - math.log(500.0))
        / (math.log(5000.0) - math.log(500.0))
    )
    assert curve.time(1000.0) == pytest.approx(expected, rel=1e-12)


def test_no_extrapolation_coverage_check():
    curve = make_curve([(100.0, 2.0), (1000.0, 0.5)])
    assert curve.covers(100.0, 1000.0)
    assert curve.covers(200.0, 900.0)
    assert not curve.covers(99.0, 1000.0)
    assert not curve.covers(100.0, 1001.0)
    # evaluation clamps instead of extrapolating
    assert curve.log_time(50.0) == pytest.approx(math.log(2.0))
    assert curve.log_time(2000.0) == pytest.approx(math.log(0.5))


# --------------------------------------------------------------------------- #
# Pair analysis
# --------------------------------------------------------------------------- #
MAIN = make_curve([(50.0, 10.0), (500.0, 1.0), (5000.0, 0.1)])
UP_STREAM = make_curve([(50.0, 20.0), (500.0, 1.5), (5000.0, 0.25)])
# ratios: 2.0 @ 50, 1.5 @ 500, 2.5 @ 5000


def test_constant_ratio_equality_is_compliant():
    main = make_curve([(100.0, 2.0), (1000.0, 0.5)])
    up = make_curve([(100.0, 4.0), (1000.0, 1.0)])  # ratio == 2 everywhere
    res = analyze_pair(main, up, 100.0, 1000.0, 2.0)
    assert res.compliant
    assert res.violations == []
    assert res.min_ratio == pytest.approx(2.0, rel=1e-9)
    # tie over the whole interval -> smaller current reported
    assert res.min_ratio_current == pytest.approx(100.0)


def test_full_interval_violation():
    res = analyze_pair(MAIN, UP_STREAM, 100.0, 1000.0, 2.0)
    assert not res.compliant
    assert res.min_ratio == pytest.approx(1.5, rel=1e-9)
    assert res.min_ratio_current == pytest.approx(500.0)
    assert len(res.violations) == 1
    v = res.violations[0]
    assert v.current_from == pytest.approx(100.0)
    assert v.current_to == pytest.approx(1000.0)
    assert v.from_included and v.to_included


def test_partial_violation_open_bounds():
    res = analyze_pair(MAIN, UP_STREAM, 50.0, 5000.0, 2.0)
    assert not res.compliant
    assert len(res.violations) == 1
    v = res.violations[0]
    # ratio(50) == 2 exactly -> equality is compliant -> 50 excluded
    assert v.current_from == pytest.approx(50.0)
    assert not v.from_included
    # root of ratio = 2 inside (500, 5000), solved analytically
    w = (math.log(2.0) - math.log(1.5)) / (math.log(2.5) - math.log(1.5))
    root = 500.0 * (5000.0 / 500.0) ** w
    assert v.current_to == pytest.approx(root, rel=1e-9)
    assert not v.to_included
    assert res.min_ratio == pytest.approx(1.5, rel=1e-9)
    assert res.min_ratio_current == pytest.approx(500.0)


def test_partial_violation_mixed_bounds():
    res = analyze_pair(MAIN, UP_STREAM, 1000.0, 3000.0, 2.0)
    assert not res.compliant
    assert len(res.violations) == 1
    v = res.violations[0]
    # ratio(1000) < 2 -> interval start included
    assert v.current_from == pytest.approx(1000.0)
    assert v.from_included
    # interior root excluded
    w = (math.log(2.0) - math.log(1.5)) / (math.log(2.5) - math.log(1.5))
    root = 500.0 * 10.0**w
    assert v.current_to == pytest.approx(root, rel=1e-9)
    assert not v.to_included
    # minimum at the interval start (ratio rises monotonically afterwards)
    expected_min = 1.5 * (2.5 / 1.5) ** (math.log10(1000.0 / 500.0))
    assert res.min_ratio == pytest.approx(expected_min, rel=1e-9)
    assert res.min_ratio_current == pytest.approx(1000.0)


def test_fully_compliant_interval():
    res = analyze_pair(MAIN, UP_STREAM, 3000.0, 5000.0, 2.0)
    assert res.compliant
    assert res.violations == []
    expected_min = 1.5 * (2.5 / 1.5) ** (math.log10(3000.0 / 500.0))
    assert res.min_ratio == pytest.approx(expected_min, rel=1e-9)
    assert res.min_ratio_current == pytest.approx(3000.0)


def test_equality_at_minimum_is_compliant():
    res = analyze_pair(MAIN, UP_STREAM, 100.0, 1000.0, 1.5)
    assert res.compliant
    assert res.violations == []
    assert res.min_ratio == pytest.approx(1.5, rel=1e-9)
    assert res.min_ratio_current == pytest.approx(500.0)


def test_degenerate_interval_single_point():
    res = analyze_pair(MAIN, UP_STREAM, 500.0, 500.0, 2.0)
    assert not res.compliant
    assert len(res.violations) == 1
    v = res.violations[0]
    assert v.current_from == v.current_to == pytest.approx(500.0)
    assert v.from_included and v.to_included
    assert res.min_ratio == pytest.approx(1.5, rel=1e-9)


def test_degenerate_interval_compliant_point():
    res = analyze_pair(MAIN, UP_STREAM, 50.0, 50.0, 2.0)
    assert res.compliant  # ratio == 2.0 exactly at 50
    assert res.violations == []


def test_breakpoints_of_both_curves_are_used():
    # Breakpoints interleaved between the two curves; the per-segment
    # violation ranges must merge into one maximal interval.
    main = make_curve([(100.0, 10.0), (300.0, 3.0), (900.0, 1.0)])
    up = make_curve([(100.0, 10.2), (500.0, 1.7), (900.0, 1.02)])
    res = analyze_pair(main, up, 100.0, 900.0, 1.05)
    assert not res.compliant
    assert len(res.violations) == 1
    v = res.violations[0]
    assert v.current_from == pytest.approx(100.0)
    assert v.current_to == pytest.approx(900.0)
    assert v.from_included and v.to_included
    # minimum ratio occurs at the upstream-only breakpoint 500
    assert res.min_ratio_current == pytest.approx(500.0)
    assert res.min_ratio == pytest.approx(1.7 / 1.8, rel=1e-9)
