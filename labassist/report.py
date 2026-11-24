"""Figure, Markdown report, JSON file and short text summary of a calibration analysis."""

from __future__ import annotations

import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .calibration import fmt_pm, fmt_r2  # noqa: E402

MARK = {"ok": "✔", "check": "⚠", "info": "•"}
BLUE, RED, ORANGE, GREY = "#2f6fae", "#c0392b", "#e08e0b", "#8a8a8a"


def public_results(results: dict) -> dict:
    """Results without the internal arrays used only for the figure."""
    return {k: v for k, v in results.items() if not k.startswith("_")}


def equation(slope: float, intercept: float) -> str:
    sign = "+" if intercept >= 0 else "−"
    return f"y = {slope:#.4g}·x {sign} {abs(intercept):#.4g}"


def _range_text(results: dict) -> str:
    rng, cu = results["range"], results["units"]["concentration"]
    text = f"{rng['low']:g}–{rng['high']:g} {cu}".strip()
    return text + {"suggested": " (suggested)", "chosen": " (chosen)"}.get(rng["mode"], "")


def _limit_label(method: str) -> str:
    return {"A": "A, blank SD", "B": "B, s_y/x", "B2": "B2, SE of intercept"}[method]


# ---------------------------------------------------------------- figure

def make_figure(results: dict, path: str) -> None:
    """Calibration plot (mean ± SD, line, 95 % band, LOD/LOQ) above a residual plot."""
    i = results["_internal"]
    x, y, sd, used = i["x"], i["y"], i["sd"], i["used"]
    f = results["ols"]
    a, b = f["intercept"], f["slope"]
    cu, su = results["units"]["concentration"], results["units"]["signal"]
    cu_l, su_l = (f" ({cu})" if cu else ""), (f" ({su})" if su else "")

    def band(xs):
        return i["t95"] * f["s_yx"] * np.sqrt(1 / f["n_points"] + (xs - i["x_mean"]) ** 2 / i["sxx"])

    xs = np.linspace(min(0.0, x[used].min()), x[used].max(), 300)
    fig, (ax, axr) = plt.subplots(2, 1, figsize=(6.6, 7.2), sharex=True,
                                  gridspec_kw={"height_ratios": [3, 1.25]})

    def draw(target, with_labels):
        lab = (lambda s: s) if with_labels else (lambda s: None)
        target.fill_between(xs, a + b * xs - band(xs), a + b * xs + band(xs), color=BLUE, alpha=0.18, lw=0,
                            label=lab("95 % confidence band"))
        target.plot(xs, a + b * xs, color=BLUE, lw=1.8,
                    label=lab(f"{equation(b, a)}   R² = {fmt_r2(f['r_squared'])}"))
        if with_labels and (~used).any():
            xe = np.linspace(x[used].max(), x.max(), 100)
            target.plot(xe, a + b * xe, color=BLUE, lw=1.1, ls=":", label="Line extended beyond the range")
        target.errorbar(x[used], y[used], yerr=sd[used], fmt="o", ms=5, mfc="white", mec="#1f1f1f",
                        ecolor="#1f1f1f", capsize=3, lw=1, label=lab("Standards, mean ± SD"))
        if (~used).any():
            target.errorbar(x[~used], y[~used], yerr=sd[~used], fmt="s", ms=5, mfc="none", mec=GREY,
                            ecolor=GREY, capsize=3, lw=1, label=lab("Outside the linear range"))
        for d in results["detection_limits"]:
            if d["method"] in ("A", "B"):
                ls = ":" if d["method"] == "A" else "--"
                tag = "blank SD" if d["method"] == "A" else "s_y/x"
                target.axvline(d["lod"], color=RED, ls=ls, lw=1.1, label=lab(f"LOD ({tag}) = {d['lod']:.3g}"))
                target.axvline(d["loq"], color=ORANGE, ls=ls, lw=1.1, label=lab(f"LOQ ({tag}) = {d['loq']:.3g}"))

    draw(ax, True)
    rising = b >= 0
    leg = ax.legend(fontsize=7.3, loc="lower right" if rising else "upper right", frameon=True,
                    framealpha=0.95, facecolor="white", edgecolor="#cccccc")
    leg.set_zorder(6)
    ax.set_ylabel(f"Response{su_l}")
    ax.set_title(f"Calibration, linear range {_range_text(results)}", fontsize=10)

    # Zoomed inset when the LOD/LOQ lines crowd near zero
    loqs = [d["loq"] for d in results["detection_limits"] if d["method"] in ("A", "B")]
    positive = x[used & (x > 0)]
    if loqs and max(loqs) < 0.15 * x.max() and positive.size:
        x_zoom = max(1.25 * max(loqs), 1.15 * positive.min())
        axin = ax.inset_axes([0.07, 0.55, 0.36, 0.38] if rising else [0.07, 0.07, 0.36, 0.38])
        draw(axin, False)
        inside = (x <= x_zoom)
        ys = np.concatenate([y[inside] - sd[inside], y[inside] + sd[inside], [a, a + b * x_zoom]])
        pad = 0.08 * (ys.max() - ys.min())
        axin.set_xlim(-0.03 * x_zoom, x_zoom)
        axin.set_ylim(ys.min() - pad, ys.max() + pad)
        axin.tick_params(labelsize=6.5)
        axin.set_title("Low range", fontsize=7)
        axin.set_zorder(5)

    residuals = y - (a + b * x)
    axr.axhline(0, color="#777", lw=1)
    axr.errorbar(x[used], residuals[used], yerr=sd[used], fmt="o", ms=4, color="#1f1f1f", capsize=2.5, lw=1)
    axr.set_ylabel(f"Residual{su_l}")
    if (~used).any():
        axr.set_title("Residuals of the points in the fit", fontsize=8)
    axr.set_xlabel(f"Concentration{cu_l}")
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- text outputs

def summary_text(results: dict, files: dict | None = None, for_assistant: bool = False) -> str:
    """Plain-text result, ordered the way the answer should be given.

    Sections: DATA, LINE, LOD / LOQ, CHECKS (items that need explaining), OTHER FINDINGS,
    then ASK THE USER (assistant only, when n is missing) and FILES. A mid-sized local
    model only has to put this into words, not decide what matters or in which order.
    """
    f, rng = results["ols"], results["range"]
    cu = results["units"]["concentration"]
    per = f"{results['units']['signal'] or 'signal units'} per {cu or 'concentration unit'}"
    read_back = ", ".join(f"({p['x']:g}, {p['y']:g}, {p['sd']:g})" for p in results["points"])
    lines = [
        f"DATA: {len(results['points'])} points read (X, Y, SD): {read_back}",
        f"  Range used: {_range_text(results)}, {rng['points_used']} of {rng['points_total']} points in the fit.",
        f"LINE: {equation(f['slope'], f['intercept'])}, R² = {fmt_r2(f['r_squared'])}",
        f"  Sensitivity (slope): {fmt_pm(f['slope'], f['se_slope'])} {per} "
        f"(95 % CI {f['ci95_slope'][0]:.4g} to {f['ci95_slope'][1]:.4g})",
        f"LOD / LOQ (k = {results['k_factor']:g}):",
    ]
    for d in results["detection_limits"]:
        lines.append(f"  {_limit_label(d['method'])} (σ = {d['sigma']:.3g}): "
                     f"LOD {d['lod']:.3g} {cu}, LOQ {d['loq']:.3g} {cu}".replace(" ,", ","))
    checks = [c for c in results["conclusions"] if c["level"] == "check"]
    others = [c for c in results["conclusions"]
              if c["level"] != "check" and c.get("topic") not in ("sensitivity", "replicates")]
    lines.append("CHECKS (explain each one):" if checks else "CHECKS: none")
    lines += [f"  ⚠ {c['text']}" for c in checks]
    lines.append("OTHER FINDINGS:")
    lines += [f"  {MARK[c['level']]} {c['text']}" for c in others]
    lines += [f"  • Note on the input: {note}" for note in results["reading_notes"]]
    if results["n_replicates"] is None:
        lines.append("ASK THE USER: How many readings are behind each mean and SD? With that number the "
                     "lack-of-fit and variance tests can also run." if for_assistant else
                     "NOTE: give --n (readings per mean and SD) to add the lack-of-fit and variance tests.")
    if files:
        lines += ["FILES:", f"  Report: {files['report']}", f"  Figure: {files['figure']}"]
    return "\n".join(lines)


def markdown_report(results: dict, figure_name: str | None = None, source: str = "") -> str:
    f, w, rng = results["ols"], results["wls"], results["range"]
    cu, su = results["units"]["concentration"], results["units"]["signal"]
    n, k = results["n_replicates"], results["k_factor"]
    per = f"{su or 'signal'}/{cu or 'conc.'}"
    out = ["# Calibration report", ""]
    meta = [f"Data: `{source}`"] if source else []
    meta += [f"replicates per point: {n if n else 'not given'}", f"k = {k:g}"]
    out += [" · ".join(meta), "", "## Key results", "", "| | |", "|---|---|",
            f"| Range used | {_range_text(results)}, {rng['points_used']} of {rng['points_total']} points |",
            f"| Calibration line | {equation(f['slope'], f['intercept'])} |",
            f"| Sensitivity (slope) | {fmt_pm(f['slope'], f['se_slope'])} {per} |",
            f"| R² | {fmt_r2(f['r_squared'])} |"]
    for d in results["detection_limits"]:
        out.append(f"| LOD / LOQ, {_limit_label(d['method'])} | {d['lod']:.3g} / {d['loq']:.3g} {cu} |")

    out += ["", "## Conclusions", ""]
    out += [f"- {MARK[c['level']]} {c['text']}" for c in results["conclusions"]]
    out += [f"- Note on input: {note}" for note in results["reading_notes"]]

    out += ["", "## Data and back-calculated concentrations", "",
            f"| X ({cu}) | Y | SD | RSD % | In fit | X found | Recovery % |".replace(" ()", ""),
            "|---|---|---|---|---|---|---|"]
    for p, bc in zip(results["points"], results["back_calculation"], strict=True):
        rsd = f"{p['rsd_percent']:.1f}" if p["rsd_percent"] is not None else "–"
        rec = f"{bc['recovery_percent']:.1f}" if "recovery_percent" in bc else "–"
        if bc.get("outside_limit"):
            rec += " ⚠"
        out.append(f"| {p['x']:g} | {p['y']:.4g} | {p['sd']:.2g} | {rsd} | {'yes' if bc['used_in_fit'] else 'no'} "
                   f"| {bc['found']:.3g} | {rec} |")

    def ci(c):
        return f"{c[0]:.4g} to {c[1]:.4g}"

    out += ["", "## Line parameters", "", "| | Unweighted (OLS) | Weighted (1/SD²) |", "|---|---|---|"]
    if w:
        out += [f"| Slope ± SE | {fmt_pm(f['slope'], f['se_slope'])} | {fmt_pm(w['slope'], w['se_slope'])} |",
                f"| 95 % CI slope | {ci(f['ci95_slope'])} | {ci(w['ci95_slope'])} |",
                f"| Intercept ± SE | {fmt_pm(f['intercept'], f['se_intercept'])} | "
                f"{fmt_pm(w['intercept'], w['se_intercept'])} |",
                f"| 95 % CI intercept | {ci(f['ci95_intercept'])} | {ci(w['ci95_intercept'])} |"]
    else:
        out += [f"| Slope ± SE | {fmt_pm(f['slope'], f['se_slope'])} | not possible (an SD is 0) |",
                f"| 95 % CI slope | {ci(f['ci95_slope'])} | |",
                f"| Intercept ± SE | {fmt_pm(f['intercept'], f['se_intercept'])} | |",
                f"| 95 % CI intercept | {ci(f['ci95_intercept'])} | |"]
    out += [f"| R² (adjusted) | {fmt_r2(f['r_squared'])} ({fmt_r2(f['adjusted_r_squared'])}) | |",
            f"| s_y/x | {f['s_yx']:.3g} | |"]

    out += ["", "## Tests", ""]
    m = results["mandel_test"]
    out.append(f"- Mandel's fitting test: TV = {m['test_value']:.3g}, F(99 %; {m['df'][0]}, {m['df'][1]}) = "
               f"{m['f_critical_99']:.3g}, p = {m['p_value']:.3g} → curvature significant: "
               f"**{'yes' if m['quadratic_better'] else 'no'}**" if m else
               "- Mandel's fitting test: not possible (needs at least 5 points)")
    ma, fa = results["mandel_test_all_points"], results["fit_all_points"]
    if ma:
        out.append(f"- Mandel's test with all points: p = {ma['p_value']:.3g} → curvature significant: "
                   f"**{'yes' if ma['quadratic_better'] else 'no'}**")
    if fa:
        out.append(f"- One line through all points would give R² = {fmt_r2(fa['r_squared'])}: a high R² "
                   "even though the response is curved.")
    lof = results["lack_of_fit_test"]
    out.append(f"- Lack-of-fit F-test: F = {lof['f_value']:.3g} (df {lof['df'][0]}, {lof['df'][1]}), "
               f"p = {lof['p_value']:.3g} → significant: **{'yes' if lof['lack_of_fit'] else 'no'}**" if lof else
               "- Lack-of-fit F-test: not possible (needs the number of replicates n ≥ 2)")
    v = results["variance_test"]
    if "f_value" in v:
        out.append(f"- Variance F-test, {v['x_lowest']:g} vs {v['x_highest']:g} {cu}: F = {v['f_value']:.3g}, "
                   f"F(99 %; {v['df'][0]}, {v['df'][1]}) = {v['f_critical_99']:.3g}, p = {v['p_value']:.3g} → "
                   f"unequal: **{'yes' if v['unequal'] else 'no'}**")
    else:
        out.append("- Variance F-test: not possible (needs n ≥ 2 and non-zero SDs)")

    out += ["", f"## Detection and quantification limits (k = {k:g})", "",
            "| Method | σ from | σ | LOD | LOQ |", "|---|---|---|---|---|"]
    for d in results["detection_limits"]:
        out.append(f"| {d['method']} | {d['sigma_from']} | {d['sigma']:.3g} | {d['lod']:.3g} {cu} | "
                   f"{d['loq']:.3g} {cu} |")

    if figure_name:
        out += ["", "## Figure", "", f"![Calibration and residuals]({figure_name})"]
    out += ["", "## Methods", "",
            "- Line: least squares on the mean values (OLS); a weighted fit (weights 1/SD²) is shown for comparison.",
            "- Linearity: Mandel's fitting test, linear vs. quadratic, 99 % (ISO 8466-1). If all points together "
            "are curved, the highest points are removed one at a time (keeping at least 5) to suggest a linear range.",
            "- Lack of fit: F-test at 5 %, with pure error from the SDs and n.",
            "- Variances: F-test of the SDs at the lowest and highest concentration, 99 % (ISO 8466-1).",
            "- Back-calculation: ±15 % of nominal, ±20 % at the lowest standard (ICH M10), used as a guide.",
            "- LOD = k·σ/S and LOQ = 10·σ/S (ICH Q2(R2)); k = 3.3 by default, k = 3 is the IUPAC 3σ convention. "
            "σ: A = SD at X = 0; B = s_y/x; B2 = standard error of the intercept.",
            "- s_y/x describes the scatter of mean values, while the blank SD describes single readings, "
            "so methods A and B estimate the noise on different bases."]
    return "\n".join(out) + "\n"


def write_outputs(results: dict, out_dir: str, stem: str, source: str = "") -> dict:
    """Write <stem>_report.md, <stem>_results.json and <stem>_calibration.png."""
    os.makedirs(out_dir, exist_ok=True)
    fig_name = f"{stem}_calibration.png"
    paths = {"figure": os.path.join(out_dir, fig_name),
             "report": os.path.join(out_dir, f"{stem}_report.md"),
             "json": os.path.join(out_dir, f"{stem}_results.json")}
    make_figure(results, paths["figure"])
    with open(paths["report"], "w", encoding="utf-8") as fh:
        fh.write(markdown_report(results, fig_name, source))
    with open(paths["json"], "w", encoding="utf-8") as fh:
        json.dump(public_results(results), fh, indent=2, ensure_ascii=False)
    return paths
