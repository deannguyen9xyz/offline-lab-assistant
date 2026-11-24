"""Tests for the calibration module.

Run from the repository folder:  python -m pytest
"""

import os

import numpy as np
import pytest

from labassist.calibration import (Points, analyse, detection_limits, fmt_pm, lack_of_fit_test,
                                   mandel_test, ols_fit, parse_points, read_points, wls_fit)

EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "example_points.csv")


# ---------------------------------------------------------------- published reference values

def test_line_matches_textbook_example():
    """Worked example in J. N. Miller and J. C. Miller, Statistics and Chemometrics for
    Analytical Chemistry (calibration chapter): b = 1.93, a = 1.52, r = 0.9989,
    s_y/x = 0.4329, s_b = 0.0409, s_a = 0.2950, LOD (3 s_y/x / b) = 0.67."""
    x = np.array([0, 2, 4, 6, 8, 10, 12], dtype=float)
    y = np.array([2.1, 5.0, 9.0, 12.6, 17.3, 21.0, 24.7])
    f = ols_fit(x, y)
    assert f["slope"] == pytest.approx(1.93, abs=0.005)
    assert f["intercept"] == pytest.approx(1.52, abs=0.005)
    assert f["r"] == pytest.approx(0.9989, abs=0.0001)
    assert f["s_yx"] == pytest.approx(0.4329, abs=0.0005)
    assert f["se_slope"] == pytest.approx(0.0409, abs=0.0005)
    assert f["se_intercept"] == pytest.approx(0.2950, abs=0.0005)
    lod_b = [d for d in detection_limits(Points(x, y, np.full(7, 0.1)), f, k=3) if d["method"] == "B"][0]
    assert lod_b["lod"] == pytest.approx(0.67, abs=0.01)


# ---------------------------------------------------------------- reading input

def test_parse_bracket_format():
    pts, notes = parse_points("(0, 0.013, 0.0008), (20, 0.050, 0.0017), (40, 0.106, 0.0021)")
    assert list(pts.x) == [0, 20, 40]
    assert list(pts.y) == [0.013, 0.050, 0.106]
    assert list(pts.sd) == [0.0008, 0.0017, 0.0021]
    assert notes == []


def test_parse_brackets_with_semicolons_and_decimal_commas():
    pts, _ = parse_points("(0; 0,013; 0,0008) (20; 0,050; 0,0017) (40; 0,106; 0,0021)")
    assert list(pts.y) == [0.013, 0.050, 0.106]


def test_parse_sorts_by_concentration():
    pts, _ = parse_points("(40, 0.106, 0.0021), (0, 0.013, 0.0008), (20, 0.050, 0.0017)")
    assert list(pts.x) == [0, 20, 40]
    assert list(pts.sd) == [0.0008, 0.0017, 0.0021]


def test_parse_excel_semicolon_table_with_columns_in_other_order():
    text = "SD;Concentration (nM);Response\n0,0008;0;0,013\n0,0017;20;0,050\n0,0021;40;0,106\n"
    pts, _ = parse_points(text)
    assert list(pts.x) == [0, 20, 40]
    assert list(pts.y) == [0.013, 0.050, 0.106]


def test_parse_delta_y_header_and_spaces():
    pts, _ = parse_points("X  Y  ΔY\n0 0.013 0.0008\n20 0.050 0.0017\n40 0.106 0.0021\n")
    assert list(pts.sd) == [0.0008, 0.0017, 0.0021]


def test_parse_pasted_table_with_units_in_header():
    pts, notes = parse_points("conc (nM)  signal (uA)  SD\n0  0.013  0.0008\n20  0.050  0.0017\n40  0.106  0.0021\n")
    assert list(pts.x) == [0, 20, 40] and list(pts.sd) == [0.0008, 0.0017, 0.0021] and notes == []


def test_missing_sd_is_an_error():
    with pytest.raises(ValueError, match="X, Y and SD"):
        parse_points("(0, 0.013), (20, 0.050), (40, 0.106)")


def test_text_is_an_error():
    with pytest.raises(ValueError, match="not a number"):
        parse_points("x,y,sd\n0,0.013,0.0008\n20,abc,0.0017\n40,0.106,0.0021\n")


def test_missing_file_is_reported():
    with pytest.raises(FileNotFoundError):
        read_points("no_such_file.csv")


# ---------------------------------------------------------------- statistics

def test_lack_of_fit_from_means_equals_test_on_raw_replicates():
    """The F value from means, SDs and n must equal the classic test on all single readings."""
    rng = np.random.default_rng(7)
    x_levels = np.array([0, 10, 20, 40, 60, 80.0])
    n = 4
    raw = np.array([0.01 + 0.002 * c + 1e-6 * c ** 2 + rng.normal(0, 0.002, n) for c in x_levels])
    means, sds = raw.mean(axis=1), raw.std(axis=1, ddof=1)

    x_all, y_all = np.repeat(x_levels, n), raw.ravel()
    line = np.polyval(np.polyfit(x_all, y_all, 1), x_all)
    ss_res = np.sum((y_all - line) ** 2)
    ss_pe = np.sum((raw - means[:, None]) ** 2)
    m = len(x_levels)
    f_raw = ((ss_res - ss_pe) / (m - 2)) / (ss_pe / (m * (n - 1)))

    lof = lack_of_fit_test(sds, ols_fit(x_levels, means)["residuals"], n)
    assert lof["f_value"] == pytest.approx(f_raw, rel=1e-9)


def test_weighted_fit_matches_numpy():
    x = np.array([0, 10, 20, 40, 80.0])
    y = np.array([0.012, 0.035, 0.058, 0.105, 0.196])
    sd = np.array([0.0008, 0.001, 0.0012, 0.0018, 0.003])
    w = wls_fit(x, y, sd)
    coef, cov = np.polyfit(x, y, 1, w=1 / sd, cov=True)
    assert w["slope"] == pytest.approx(coef[0], rel=1e-9)
    assert w["intercept"] == pytest.approx(coef[1], rel=1e-9)
    assert w["se_slope"] == pytest.approx(np.sqrt(cov[0, 0]), rel=1e-6)
    assert w["se_intercept"] == pytest.approx(np.sqrt(cov[1, 1]), rel=1e-6)


def test_weighted_equals_unweighted_when_sds_are_equal():
    x = np.array([0, 10, 20, 40, 80.0])
    y = np.array([0.012, 0.035, 0.058, 0.105, 0.196])
    w, f = wls_fit(x, y, np.full(5, 0.002)), ols_fit(x, y)
    assert w["slope"] == pytest.approx(f["slope"])
    assert w["se_slope"] == pytest.approx(f["se_slope"])


def test_mandel_detects_curvature_but_not_noise():
    x = np.array([0, 20, 40, 60, 80, 100, 150, 200.0])
    curved = 0.01 + 0.002 * x - 4e-6 * x ** 2
    assert mandel_test(x, curved + np.array([1, -1, 1, -1, 1, -1, 1, -1]) * 1e-4)["quadratic_better"]
    rng = np.random.default_rng(3)
    straight = 0.01 + 0.002 * x + rng.normal(0, 0.002, x.size)
    assert not mandel_test(x, straight)["quadratic_better"]


def test_limits_use_blank_sd_and_line_sd():
    pts, _ = parse_points("(0, 0.013, 0.0008), (20, 0.050, 0.0017), (40, 0.106, 0.0021), "
                          "(60, 0.150, 0.0024), (80, 0.195, 0.0027)")
    f = ols_fit(pts.x, pts.y)
    lim = {d["method"]: d for d in detection_limits(pts, f, k=3.3)}
    assert lim["A"]["lod"] == pytest.approx(3.3 * 0.0008 / f["slope"])
    assert lim["A"]["loq"] == pytest.approx(10 * 0.0008 / f["slope"])
    assert lim["B"]["lod"] == pytest.approx(3.3 * f["s_yx"] / f["slope"])


def test_decreasing_signal_gives_positive_limits():
    """Signal-off sensors: the response falls with concentration."""
    pts, _ = parse_points("(0, 1.00, 0.01), (10, 0.90, 0.01), (20, 0.81, 0.012), (30, 0.70, 0.012), "
                          "(40, 0.61, 0.015)")
    res = analyse(pts, n_replicates=3)
    assert res["ols"]["slope"] < 0
    assert all(d["lod"] > 0 for d in res["detection_limits"])


def test_no_zero_point_means_no_blank_method():
    pts, _ = parse_points("(5, 0.025, 0.0008), (10, 0.036, 0.001), (20, 0.059, 0.001), "
                          "(40, 0.105, 0.002), (80, 0.196, 0.003)")
    res = analyse(pts, n_replicates=3)
    assert [d["method"] for d in res["detection_limits"]] == ["B", "B2"]
    assert any("method A" in c["text"] for c in res["conclusions"])


def test_without_n_the_replicate_tests_are_skipped():
    pts, _ = read_points(EXAMPLE)
    res = analyse(pts)
    assert res["lack_of_fit_test"] is None
    assert "f_value" not in res["variance_test"]
    assert any("(n) was not given" in c["text"] for c in res["conclusions"])


def test_four_points_are_too_few_for_mandel():
    pts, _ = parse_points("(0, 0.013, 0.0008), (20, 0.050, 0.0017), (40, 0.106, 0.0021), (80, 0.195, 0.0027)")
    res = analyse(pts, n_replicates=3)
    assert res["mandel_test"] is None
    assert any("too few for Mandel" in c["text"] for c in res["conclusions"])


# ---------------------------------------------------------------- example data set

def test_example_data_curves_above_100_nM():
    pts, _ = read_points(EXAMPLE)
    res = analyse(pts, n_replicates=3, conc_unit="nM")
    assert res["mandel_test_all_points"]["quadratic_better"]
    assert res["range"]["mode"] == "suggested"
    assert res["range"]["high"] == 100
    assert not res["mandel_test"]["quadratic_better"]
    assert not res["lack_of_fit_test"]["lack_of_fit"]


def test_chosen_range_is_respected():
    pts, _ = read_points(EXAMPLE)
    res = analyse(pts, n_replicates=3, x_range=(0, 60))
    assert res["range"]["mode"] == "chosen"
    assert res["range"]["points_used"] == 6


def test_plus_minus_formatting():
    assert fmt_pm(0.0022966, 0.0000060) == "0.0022966 ± 0.0000060"
    assert fmt_pm(1.5179, 0.2949) == "1.52 ± 0.29"
    assert fmt_pm(-1.0e11, 2.0e9) == "(-1.000 ± 0.020)×10^11"
    assert fmt_pm(3.2e-6, 1.1e-7) == "(3.20 ± 0.11)×10^-6"


# ---------------------------------------------------------------- data that should not break the analysis

def test_flat_response_gives_a_clear_error():
    pts, _ = parse_points("(0, 0.5, 0.01), (10, 0.5, 0.01), (20, 0.5, 0.01), (30, 0.5, 0.01)")
    with pytest.raises(ValueError, match="does not change with concentration"):
        analyse(pts)


def test_points_exactly_on_a_line_do_not_crash():
    pts, _ = parse_points("(0, 0.0, 0.1), (1, 1.0, 0.1), (2, 2.0, 0.1), (3, 3.0, 0.1)")
    res = analyse(pts)
    assert any("s_y/x = 0" in c["text"] for c in res["conclusions"])


def test_slope_not_significant_is_flagged_first():
    pts, _ = parse_points("(0, 0.50, 0.02), (10, 0.52, 0.02), (20, 0.49, 0.02), (30, 0.53, 0.02), (40, 0.50, 0.02)")
    first = analyse(pts)["conclusions"][0]
    assert first["level"] == "check" and "not significantly different from zero" in first["text"]
