"""Calibration linearity and detection limits from (X, Y, SD) points.

Input: one point per concentration level
    X  = concentration of the prepared standard (for example nM)
    Y  = mean sensor response at that concentration
    SD = standard deviation of Y from the replicate readings

All numbers are computed here. A language model that calls this code only
explains the results; it never calculates them itself.

Methods
- Ordinary least squares (OLS): slope, intercept, standard errors, 95 %
  confidence intervals, R², residual standard deviation s_y/x.
- Weighted least squares (WLS, weights 1/SD²), to see whether unequal noise
  changes the line.
- Mandel's fitting test (linear vs. quadratic, 99 %; ISO 8466-1). Needs at
  least 5 points. If all points together are curved, the highest points are
  removed one at a time to suggest a linear range.
- Lack-of-fit F-test (95 %). Needs the number of replicates n per point.
- Homogeneity of variances: F-test of the SDs at the lowest and highest
  concentration (99 %; ISO 8466-1). Needs n.
- Back-calculated concentrations, compared with ±15 % of nominal (±20 % at
  the lowest standard) as a guide (ICH M10).
- LOD = k·σ/S and LOQ = 10·σ/S (S = slope), with σ from
    A   the SD of the blank (the point at X = 0),
    B   the residual standard deviation of the line, s_y/x,
    B2  the standard error of the intercept (an alternative named in ICH Q2).
  k = 3.3 (ICH Q2(R2)) by default; k = 3 gives the IUPAC 3σ convention.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import numpy as np
from scipy import stats

NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
POINT_GROUP_START = re.compile(r"[(\[]\s*[-+]?[\d.]")
NAN = float("nan")


def fmt_pm(value: float, se: float) -> str:
    """'value ± se' with the SE to 2 significant digits and the value to the same decimal place.

    Very large or very small values use a shared power of ten: (-1.000 ± 0.020)×10^11.
    """
    if not np.isfinite(se) or se <= 0:
        return f"{value:.4g}"
    exponent = 0
    if value != 0 and not 1e-3 <= abs(value) < 1e4:
        exponent = int(np.floor(np.log10(abs(value))))
        value, se = value / 10 ** exponent, se / 10 ** exponent
    decimals = max(0, 1 - int(np.floor(np.log10(se))))
    text = f"{value:.{decimals}f} ± {se:.{decimals}f}"
    return f"({text})×10^{exponent}" if exponent else text


def fmt_r2(r2: float) -> str:
    """R² with enough decimals that values close to 1 do not print as 1.0000."""
    return f"{r2:.6f}" if r2 > 0.9999 else f"{r2:.4f}"


@dataclass
class Points:
    """Calibration points sorted by concentration: X, mean response Y, SD of Y."""

    x: np.ndarray
    y: np.ndarray
    sd: np.ndarray

    def __len__(self) -> int:
        return len(self.x)

    def where(self, mask: np.ndarray) -> "Points":
        return Points(self.x[mask], self.y[mask], self.sd[mask])


# ---------------------------------------------------------------- reading input

def _role(name: str) -> str | None:
    """Guess whether a column header means X, Y or SD."""
    low = name.strip().lower()
    if any(ch in low for ch in "δσ±") or low.startswith(
            ("sd", "s.d", "std", "dev", "err", "dy", "delta", "sigma", "uncert")):
        return "sd"
    word = re.match(r"[a-z]*", low).group(0)
    if word == "i" or word.startswith(("resp", "sig", "mean", "curr", "abs", "intens", "y")):
        return "y"
    if word == "c" or word.startswith(("conc", "x")):
        return "x"
    return None


def parse_points(text: str) -> tuple[Points, list[str]]:
    """Read points written as '(X, Y, SD), (X, Y, SD), ...' or as a table.

    Square brackets work too: [[0, 0.013, 0.0008], [20, 0.050, 0.0017]].
    A table may separate columns with commas, semicolons, tabs or spaces and
    may have a header row. With semicolons, tabs or spaces, decimal commas are
    accepted (Excel with Polish, Estonian or German settings).
    Returns the points sorted by X and a list of notes about the reading.
    """
    notes: list[str] = []
    lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    if not lines:
        raise ValueError("No data found.")

    if POINT_GROUP_START.search(" ".join(lines)):  # "(20, 0.050, ..." or "[20, ..." but not "Conc (nM)"
        rows = []
        for i, group in enumerate(re.findall(r"[(\[]([^()\[\]]*)[)\]]", " ".join(lines)), 1):
            if ";" in group:  # (0; 0,013; 0,0008) with decimal commas
                group = group.replace(",", ".")
            numbers = NUMBER.findall(group)
            if len(numbers) != 3:
                raise ValueError(
                    f"Point {i} has {len(numbers)} numbers; each point needs exactly X, Y and SD, "
                    "for example (20, 0.050, 0.0017).")
            rows.append(numbers)
    else:
        first = lines[0]
        delim = ";" if ";" in first else "\t" if "\t" in first else "," if "," in first else None
        table = []
        for ln in lines:
            cells = [c.strip() for c in (ln.split(delim) if delim else ln.split()) if c.strip()]
            if delim != ",":
                cells = [c.replace(",", ".") for c in cells]
            table.append(cells)
        header = None
        if not all(NUMBER.fullmatch(c) for c in table[0]):
            header = table.pop(0)
            if delim is None:  # "conc (nM)  signal  SD" split on spaces: drop the unit-only pieces
                header = [h for h in header if not re.fullmatch(r"[(\[].*[)\]]", h)]
        if not table:
            raise ValueError("No data rows found.")
        order = [0, 1, 2]
        if header:
            roles = [_role(h) for h in header]
            if all(r in roles for r in ("x", "y", "sd")):
                order = [roles.index("x"), roles.index("y"), roles.index("sd")]
            elif len(header) == 3:
                notes.append(f"Column names {header} not recognised; read as X, Y, SD in that order.")
            else:
                raise ValueError(f"Cannot tell which columns are X, Y and SD from the header {header}. "
                                 "Use the column names concentration, response, sd.")
        rows = []
        for i, cells in enumerate(table, 1):
            if header is None and len(cells) != 3:
                raise ValueError(f"Data row {i} has {len(cells)} values; each row needs X, Y and SD.")
            if len(cells) <= max(order):
                raise ValueError(f"Data row {i} is missing values; each row needs X, Y and SD.")
            rows.append([cells[j] for j in order])

    values = []
    for i, row in enumerate(rows, 1):
        try:
            values.append([float(v) for v in row])
        except ValueError:
            raise ValueError(f"Point {i} ({', '.join(row)}) contains a value that is not a number.") from None
    if len(values) < 3:
        raise ValueError(f"Only {len(values)} points found; at least 3 are needed.")
    arr = np.array(values, dtype=float)
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    x, y, sd = arr[:, 0], arr[:, 1], arr[:, 2]
    if np.any(x < 0):
        raise ValueError("Concentrations (X) cannot be negative.")
    if np.any(sd < 0):
        raise ValueError("SD values cannot be negative.")
    if len(np.unique(x)) < len(x):
        notes.append("Some concentrations appear more than once; give one point (mean ± SD) per level.")
    return Points(x, y, sd), notes


def read_points(source: str) -> tuple[Points, list[str]]:
    """Read points from a file path, or from text when `source` is not a file."""
    if os.path.isfile(source):
        with open(source, encoding="utf-8-sig") as fh:
            return parse_points(fh.read())
    one_word = "\n" not in source and "(" not in source
    if one_word and source.strip().lower().endswith((".csv", ".txt", ".tsv")):
        raise FileNotFoundError(f"File not found: {source}")
    return parse_points(source)


# ---------------------------------------------------------------- line fits

def ols_fit(x: np.ndarray, y: np.ndarray) -> dict:
    """Unweighted least-squares line y = intercept + slope·x."""
    n, df = len(x), len(x) - 2
    x_mean, y_mean = float(x.mean()), float(y.mean())
    sxx = float(np.sum((x - x_mean) ** 2))
    if sxx == 0:
        raise ValueError("All concentrations are equal; no line can be fitted.")
    slope = float(np.sum((x - x_mean) * (y - y_mean)) / sxx)
    intercept = y_mean - slope * x_mean
    residuals = y - (intercept + slope * x)
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((y - y_mean) ** 2))
    s_yx = float(np.sqrt(ss_res / df)) if df > 0 else NAN
    se_slope = s_yx / np.sqrt(sxx)
    se_intercept = s_yx * np.sqrt(1 / n + x_mean ** 2 / sxx)
    t95 = float(stats.t.ppf(0.975, df)) if df > 0 else NAN
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else NAN
    return {
        "n_points": n,
        "slope": slope,
        "se_slope": float(se_slope),
        "ci95_slope": [slope - t95 * se_slope, slope + t95 * se_slope],
        "intercept": intercept,
        "se_intercept": float(se_intercept),
        "ci95_intercept": [intercept - t95 * se_intercept, intercept + t95 * se_intercept],
        "r_squared": float(r2),
        "adjusted_r_squared": float(1 - (1 - r2) * (n - 1) / df) if df > 0 else NAN,
        "r": float(np.sign(slope) * np.sqrt(max(r2, 0.0))) if ss_tot > 0 else NAN,
        "s_yx": s_yx,
        "residuals": residuals,
        "x_mean": x_mean,
        "sxx": sxx,
        "t95": t95,
    }


def wls_fit(x: np.ndarray, y: np.ndarray, sd: np.ndarray) -> dict | None:
    """Weighted least squares with weights 1/SD² (relative weights)."""
    if np.any(sd <= 0) or len(x) < 3:
        return None
    w = 1 / sd ** 2
    df = len(x) - 2
    w_sum = float(w.sum())
    xw, yw = float(np.sum(w * x) / w_sum), float(np.sum(w * y) / w_sum)
    sxxw = float(np.sum(w * (x - xw) ** 2))
    slope = float(np.sum(w * (x - xw) * (y - yw)) / sxxw)
    intercept = yw - slope * xw
    res = y - (intercept + slope * x)
    s2 = float(np.sum(w * res ** 2) / df)
    se_slope = float(np.sqrt(s2 / sxxw))
    se_intercept = float(np.sqrt(s2 * (1 / w_sum + xw ** 2 / sxxw)))
    t95 = float(stats.t.ppf(0.975, df))
    return {
        "weights": "1/SD^2",
        "slope": slope,
        "se_slope": se_slope,
        "ci95_slope": [slope - t95 * se_slope, slope + t95 * se_slope],
        "intercept": intercept,
        "se_intercept": se_intercept,
        "ci95_intercept": [intercept - t95 * se_intercept, intercept + t95 * se_intercept],
    }


# ---------------------------------------------------------------- tests

def mandel_test(x: np.ndarray, y: np.ndarray) -> dict | None:
    """Mandel's fitting test: does a quadratic fit significantly better (99 %)?"""
    m = len(x)
    if m < 5 or len(np.unique(x)) < 4:
        return None
    lin, quad = np.polyfit(x, y, 1), np.polyfit(x, y, 2)
    ss_lin = float(np.sum((y - np.polyval(lin, x)) ** 2))
    ss_quad = float(np.sum((y - np.polyval(quad, x)) ** 2))
    df = m - 3
    f_crit = float(stats.f.ppf(0.99, 1, df))
    if ss_quad <= 0:
        tv = float("inf") if ss_lin > 0 else 0.0
    else:
        tv = (ss_lin - ss_quad) / (ss_quad / df)
    return {
        "test_value": tv,
        "f_critical_99": f_crit,
        "df": [1, df],
        "p_value": float(stats.f.sf(tv, 1, df)),
        "quadratic_better": bool(tv > f_crit),
        "shape": "flattens" if np.sign(quad[0]) != np.sign(lin[0]) else "steepens",
    }


def lack_of_fit_test(sd: np.ndarray, residuals: np.ndarray, n_rep: int | None) -> dict | None:
    """Lack-of-fit F-test from mean values, their SDs and n replicates per point (5 %)."""
    m = len(sd)
    if n_rep is None or n_rep < 2 or m < 3:
        return None
    ss_pure_error = (n_rep - 1) * float(np.sum(sd ** 2))
    if ss_pure_error <= 0:
        return None
    df_lof, df_pe = m - 2, m * (n_rep - 1)
    ss_lof = n_rep * float(np.sum(residuals ** 2))
    f_value = (ss_lof / df_lof) / (ss_pure_error / df_pe)
    p = float(stats.f.sf(f_value, df_lof, df_pe))
    return {
        "f_value": f_value,
        "df": [df_lof, df_pe],
        "f_critical_95": float(stats.f.ppf(0.95, df_lof, df_pe)),
        "p_value": p,
        "lack_of_fit": bool(p < 0.05),
    }


def variance_test(x: np.ndarray, sd: np.ndarray, n_rep: int | None) -> dict:
    """Compare the SD at the lowest and highest concentration (F-test, 99 %, needs n)."""
    i_lo, i_hi = int(np.argmin(x)), int(np.argmax(x))
    s_lo, s_hi = float(sd[i_lo]), float(sd[i_hi])
    out = {
        "x_lowest": float(x[i_lo]), "sd_lowest": s_lo,
        "x_highest": float(x[i_hi]), "sd_highest": s_hi,
        "sd_ratio": s_hi / s_lo if s_lo > 0 else None,
    }
    if n_rep and n_rep >= 2 and s_lo > 0 and s_hi > 0:
        df = n_rep - 1
        f_value = max(s_lo, s_hi) ** 2 / min(s_lo, s_hi) ** 2
        f_crit = float(stats.f.ppf(0.99, df, df))
        out.update({
            "f_value": f_value,
            "df": [df, df],
            "f_critical_99": f_crit,
            "p_value": float(stats.f.sf(f_value, df, df)),
            "unequal": bool(f_value > f_crit),
        })
    return out


def suggest_upper_limit(points: Points) -> float | None:
    """Highest concentration up to which Mandel's test passes (removing top points, keeping >= 5)."""
    for m in range(len(points) - 1, 4, -1):
        sub = points.where(np.arange(len(points)) < m)
        test = mandel_test(sub.x, sub.y)
        if test is None:
            return None
        if not test["quadratic_better"]:
            return float(sub.x.max())
    return None


# ---------------------------------------------------------------- limits

def detection_limits(all_points: Points, fit: dict, k: float = 3.3) -> list[dict]:
    """LOD = k·σ/S and LOQ = 10·σ/S for each available σ."""
    s = abs(fit["slope"])
    out = []
    zero = np.flatnonzero(all_points.x == 0)
    if zero.size and all_points.sd[zero[0]] > 0:
        s0 = float(all_points.sd[zero[0]])
        out.append({"method": "A", "sigma_from": "SD of the blank (point at X = 0)",
                    "sigma": s0, "lod": k * s0 / s, "loq": 10 * s0 / s})
    out.append({"method": "B", "sigma_from": "residual SD of the line (s_y/x)",
                "sigma": fit["s_yx"], "lod": k * fit["s_yx"] / s, "loq": 10 * fit["s_yx"] / s})
    out.append({"method": "B2", "sigma_from": "standard error of the intercept (ICH alternative)",
                "sigma": fit["se_intercept"], "lod": k * fit["se_intercept"] / s,
                "loq": 10 * fit["se_intercept"] / s})
    return out


def back_calculation(all_points: Points, used: np.ndarray, fit: dict) -> list[dict]:
    """Concentration found from each mean response, and recovery against nominal X."""
    found = (all_points.y - fit["intercept"]) / fit["slope"]
    positive = all_points.x[used & (all_points.x > 0)]
    lowest = float(positive.min()) if positive.size else None
    rows = []
    for xi, fi, in_fit in zip(all_points.x, found, used, strict=True):
        row = {"x": float(xi), "found": float(fi), "used_in_fit": bool(in_fit)}
        if xi > 0:
            recovery = 100 * fi / xi
            limit = 20 if lowest is not None and xi == lowest else 15
            row.update({"recovery_percent": float(recovery), "limit_percent": limit,
                        "outside_limit": bool(in_fit and abs(recovery - 100) > limit)})
        rows.append(row)
    return rows


# ---------------------------------------------------------------- analysis

def analyse(points: Points, n_replicates: int | None = None, k: float = 3.3,
            x_range: tuple[float, float] | None = None, auto_range: bool = True,
            conc_unit: str = "", signal_unit: str = "",
            reading_notes: list[str] | None = None) -> dict:
    """Full analysis. Returns a dict; keys starting with '_' are for the figure only."""
    if n_replicates is not None and n_replicates < 1:
        raise ValueError("The number of replicates must be at least 1.")
    all_pts = points
    full_mandel = mandel_test(all_pts.x, all_pts.y)
    mode = "all points"
    upper = None
    if x_range is not None:
        lo, hi = x_range
        used = (all_pts.x >= lo) & (all_pts.x <= hi)
        mode = "chosen"
    else:
        used = np.ones(len(all_pts), dtype=bool)
        if auto_range and full_mandel and full_mandel["quadratic_better"]:
            upper = suggest_upper_limit(all_pts)
            if upper is not None:
                used = all_pts.x <= upper
                mode = "suggested"
    pts = all_pts.where(used)
    if len(pts) < 3:
        raise ValueError("At least 3 points are needed in the selected range.")

    fit = ols_fit(pts.x, pts.y)
    if fit["slope"] == 0:
        raise ValueError("The response does not change with concentration (slope = 0), so no calibration "
                         "line, LOD or LOQ can be calculated. Check the data.")
    wfit = wls_fit(pts.x, pts.y, pts.sd)
    mandel = mandel_test(pts.x, pts.y)
    lof = lack_of_fit_test(pts.sd, fit["residuals"], n_replicates)
    var = variance_test(pts.x, pts.sd, n_replicates)
    limits = detection_limits(all_pts, fit, k)
    back = back_calculation(all_pts, used, fit)

    full_fit = None
    if not used.all():
        f_all = ols_fit(all_pts.x, all_pts.y)
        full_fit = {"slope": f_all["slope"], "intercept": f_all["intercept"],
                    "r_squared": f_all["r_squared"]}

    results = {
        "points": [{"x": float(a), "y": float(b), "sd": float(c),
                    "rsd_percent": float(100 * c / abs(b)) if b != 0 else None}
                   for a, b, c in zip(all_pts.x, all_pts.y, all_pts.sd, strict=True)],
        "n_replicates": n_replicates,
        "k_factor": k,
        "units": {"concentration": conc_unit, "signal": signal_unit},
        "range": {"mode": mode, "low": float(pts.x.min()), "high": float(pts.x.max()),
                  "points_used": int(used.sum()), "points_total": len(all_pts)},
        "ols": {key: v for key, v in fit.items() if key not in ("residuals", "x_mean", "sxx", "t95")},
        "wls": wfit,
        "mandel_test": mandel,
        "mandel_test_all_points": full_mandel if mode != "all points" else None,
        "fit_all_points": full_fit,
        "lack_of_fit_test": lof,
        "variance_test": var,
        "back_calculation": back,
        "detection_limits": limits,
        "reading_notes": list(reading_notes or []),
    }
    results["conclusions"] = _conclusions(results, all_pts, pts)
    results["_internal"] = {"x": all_pts.x, "y": all_pts.y, "sd": all_pts.sd, "used": used,
                            "x_mean": fit["x_mean"], "sxx": fit["sxx"], "t95": fit["t95"]}
    return results


def _conclusions(r: dict, all_pts: Points, pts: Points) -> list[dict]:
    """Plain-language findings derived by fixed rules (level: ok, check or info)."""
    out: list[dict] = []

    def add(level: str, text: str, topic: str = "") -> None:
        out.append({"level": level, "text": text, "topic": topic})

    cu = f" {r['units']['concentration']}" if r["units"]["concentration"] else ""
    per = r["units"]["concentration"] or "concentration unit"
    sig = r["units"]["signal"] or "signal units"
    f, rng, n = r["ols"], r["range"], r["n_replicates"]
    mandel, lof, var = r["mandel_test"], r["lack_of_fit_test"], r["variance_test"]

    # Is there a response at all?
    if f["ci95_slope"][0] <= 0 <= f["ci95_slope"][1]:
        add("check", "The slope is not significantly different from zero (its 95 % CI includes 0): the response "
                     "does not clearly change with concentration in this range, so the line, the LOD and the LOQ "
                     "are not meaningful. Check the sensor response or the concentration range.")

    # Linearity
    if rng["mode"] == "suggested":
        fm = r["mandel_test_all_points"]
        add("check", f"With all points ({all_pts.x.min():g}–{all_pts.x.max():g}{cu}) the response is curved "
                     f"(Mandel's test, p = {fm['p_value']:.2g}): it {fm['shape']} at high concentration. "
                     f"Without the points above {rng['high']:g}{cu} the curvature is no longer significant "
                     f"(p = {mandel['p_value']:.2g}). Suggested linear range: {rng['low']:g}–{rng['high']:g}{cu}; "
                     "all results below are for this range.")
    elif mandel is None:
        add("check", f"Only {rng['points_used']} points in the fitted range: too few for Mandel's test "
                     "(needs at least 5). Linearity then rests on R² and the residual plot, which is weak evidence.")
    elif mandel["quadratic_better"]:
        where = "the chosen range" if rng["mode"] == "chosen" else "all points"
        extra = " Narrow the range." if rng["mode"] == "chosen" else (
            " Removing the highest points does not fix it while keeping at least 5 points, "
            "so a straight line may not suit these data.")
        add("check", f"The response is curved over {where} (Mandel's test, p = {mandel['p_value']:.2g}): "
                     f"it {mandel['shape']} at high concentration.{extra}")
    elif mandel["p_value"] < 0.05:
        add("check", f"Borderline linearity over {rng['low']:g}–{rng['high']:g}{cu}: Mandel's test is not "
                     f"significant at the 99 % level used here (p = {mandel['p_value']:.2g}), but would be at 95 %; "
                     f"the response may {mandel['shape'][:-1]} slightly at high concentration. "
                     "Check the residual plot.")
    else:
        add("ok", f"Linear over {rng['low']:g}–{rng['high']:g}{cu}: Mandel's test finds no significant "
                  f"curvature (p = {mandel['p_value']:.2g}).")

    if lof is not None:
        if lof["lack_of_fit"]:
            add("check", f"The lack-of-fit test is significant (p = {lof['p_value']:.2g}): the means deviate "
                         "from the line more than the replicate SDs explain. Possible reasons: remaining "
                         "curvature, an error at one standard, or SDs from repeat readings on one sensor while "
                         "the means also vary between sensors or days.")
        else:
            add("ok", f"The lack-of-fit test is not significant (p = {lof['p_value']:.2g}): the scatter of the "
                      "means around the line matches the replicate SDs.")
    elif n is None:
        add("info", "The number of replicates per point (n) was not given, so the lack-of-fit and variance "
                    "tests were skipped.", topic="replicates")
    elif n < 2:
        add("info", "With n = 1 there are no replicates, so the lack-of-fit and variance tests are not possible.",
            topic="replicates")

    # Sensitivity, R², intercept
    add("info", f"Sensitivity (slope) = {fmt_pm(f['slope'], f['se_slope'])} {sig} per {per} "
                f"(95 % CI {f['ci95_slope'][0]:.4g} to {f['ci95_slope'][1]:.4g}). R² = {fmt_r2(f['r_squared'])}; "
                "R² alone does not prove linearity.", topic="sensitivity")
    ci_a = f["ci95_intercept"]
    zero = np.flatnonzero(all_pts.x == 0)
    if ci_a[0] <= 0 <= ci_a[1]:
        add("ok", f"The intercept ({f['intercept']:.3g}) is not significantly different from 0.")
    elif zero.size:
        y0 = float(all_pts.y[zero[0]])
        if ci_a[0] <= y0 <= ci_a[1]:
            add("info", f"The intercept ({f['intercept']:.3g}) differs from 0 but agrees with the measured blank "
                        f"response ({y0:.3g}): the line starts at the blank signal, as expected for responses "
                        "that are not blank-corrected.")
        else:
            add("check", f"The intercept ({f['intercept']:.3g}, 95 % CI {ci_a[0]:.3g} to {ci_a[1]:.3g}) differs "
                         f"from 0 and from the measured blank response ({y0:.3g}). Check for a constant bias "
                         "or a curved low-concentration range.")
    else:
        add("info", f"The intercept ({f['intercept']:.3g}) differs from 0. This is expected if the response is "
                    "not blank-corrected; otherwise check for a constant bias.")

    # Noise and weighting
    if var["sd_ratio"] is not None:
        text = (f"The SD changes from {var['sd_lowest']:.2g} at {var['x_lowest']:g}{cu} to "
                f"{var['sd_highest']:.2g} at {var['x_highest']:g}{cu} (×{var['sd_ratio']:.1f}).")
        if "f_value" in var:
            if var["unequal"]:
                text += f" The F-test (ISO 8466-1, 99 %) confirms unequal variances (p = {var['p_value']:.2g})."
            else:
                text += (f" With n = {n} readings per point, the F-test (ISO 8466-1, 99 %) cannot confirm "
                         f"a difference (p = {var['p_value']:.2g}).")
        level = "info"
        w = r["wls"]
        if w is not None:
            diff = w["slope"] - f["slope"]
            pct = 100 * diff / f["slope"]
            if abs(diff) <= f["se_slope"]:
                text += (f" A weighted fit (1/SD²) gives slope {w['slope']:.4g} ({pct:+.1f} %), within the "
                         "slope's standard error, so weighting changes little.")
            else:
                text += (f" A weighted fit (1/SD²) gives slope {w['slope']:.4g} ({pct:+.1f} %), more than the "
                         "slope's standard error: weighting matters here, and the weighted line follows the "
                         "more precise low-concentration points more closely.")
                level = "check"
        add(level, text)

    # Back-calculation
    outside = [b for b in r["back_calculation"] if b.get("outside_limit")]
    if outside:
        listed = "; ".join(f"{b['x']:g}{cu} found as {b['found']:.3g} ({b['recovery_percent']:.0f} %)"
                           for b in outside)
        add("check", f"Back-calculated concentrations outside ±15 % of nominal (±20 % at the lowest standard, "
                     f"ICH M10 used as a guide): {listed}.")
    elif any(b["used_in_fit"] and b["x"] > 0 for b in r["back_calculation"]):
        worst = max((b for b in r["back_calculation"] if b["used_in_fit"] and b["x"] > 0),
                    key=lambda b: abs(b["recovery_percent"] - 100))
        add("ok", f"Every standard back-calculates close to its nominal value: the largest deviation is "
                  f"{abs(worst['recovery_percent'] - 100):.1f} % (at {worst['x']:g}{cu}). The limits used are 15 %, "
                  "or 20 % for the lowest standard (ICH M10 limits, used here only as a guide).")

    # Detection limits
    lim = {d["method"]: d for d in r["detection_limits"]}
    a_lim, b_lim = lim.get("A"), lim["B"]
    if a_lim is None:
        add("info", "There is no point at X = 0 (or its SD is 0), so the blank-based LOD (method A) "
                    "was not calculated.")
    elif b_lim["lod"] == 0:
        add("info", "The points lie exactly on the line (s_y/x = 0), so method B gives no meaningful LOD; "
                    "use method A, which is based on the blank SD.")
    else:
        ratio = b_lim["lod"] / a_lim["lod"]
        both = f"(A: {a_lim['lod']:.3g}{cu}, B: {b_lim['lod']:.3g}{cu})"
        if ratio > 1.5:
            text = (f"The line-based LOD is {ratio:.1f}× the blank-based LOD {both}: the means scatter around "
                    f"the line (s_y/x = {b_lim['sigma']:.2g}) more than the blank readings do "
                    f"(SD = {a_lim['sigma']:.2g}). Method B is the more conservative estimate.")
        elif ratio < 1 / 1.5:
            text = (f"The blank-based LOD is {1 / ratio:.1f}× the line-based LOD {both}: the blank readings "
                    f"(SD = {a_lim['sigma']:.2g}) are noisier than the scatter of the means around the line "
                    f"(s_y/x = {b_lim['sigma']:.2g}). Method A is the more conservative estimate.")
            if n and n > 1:
                text += (f" Part of this gap is expected: s_y/x comes from means of {n} readings, "
                         "while the blank SD describes single readings.")
        else:
            text = f"The blank-based and line-based LODs agree within a factor of 1.5 {both}."
        add("info", text)

    df = f["n_points"] - 2
    if df < 3:
        add("info", f"With {f['n_points']} points, s_y/x rests on only {df} degree{'s' if df != 1 else ''} "
                    "of freedom, so the line-based LOD (method B) is a rough estimate.")

    main = [d for d in (a_lim, b_lim) if d is not None]
    positive = pts.x[pts.x > 0]
    if positive.size:
        x1 = float(positive.min())
        worst_lod = max(main, key=lambda d: d["lod"])
        worst_loq = max(main, key=lambda d: d["loq"])
        if x1 < worst_lod["lod"]:
            add("check", f"The lowest standard ({x1:g}{cu}) is below the LOD from method {worst_lod['method']} "
                         f"({worst_lod['lod']:.3g}{cu}): by that estimate it cannot be told apart from the blank.")
        elif x1 < worst_loq["loq"]:
            add("info", f"The lowest standard ({x1:g}{cu}) is above the LODs but below the LOQ from method "
                        f"{worst_loq['method']} ({worst_loq['loq']:.3g}{cu}): detectable, but not quantifiable "
                        "by that estimate. Start the quantitative range at the LOQ.")
        if max(d["lod"] for d in main) < x1:
            add("info", f"The LODs lie below the lowest standard ({x1:g}{cu}), so they are extrapolated from "
                        "the line. Measuring a standard near the LOQ would confirm them experimentally.")
    if a_lim is not None and n and n >= 2:
        df = n - 1
        low = np.sqrt(df / stats.chi2.ppf(0.975, df))
        high = np.sqrt(df / stats.chi2.ppf(0.025, df))
        add("info", f"The blank SD comes from n = {n} readings; its 95 % confidence interval is "
                    f"{low:.2f}–{high:.1f}× the value, so the blank-based LOD is uncertain by the same factor. "
                    "More blank readings narrow this.")
    return out
