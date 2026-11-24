"""Check calibration linearity and compute LOD/LOQ from (X, Y, SD) points.

Each point is one concentration level:
    X  = concentration of the prepared standard (for example nM)
    Y  = mean sensor response
    SD = standard deviation of Y

Examples
    python calibrate.py data/example_points.csv --n 3 --conc-unit nM
    python calibrate.py --points "(0, 0.013, 0.0008), (20, 0.050, 0.0017), (40, 0.106, 0.0021)" --n 3
    python calibrate.py data/example_points.csv --n 3 --range 0 100

Writes a Markdown report, a figure and a JSON file with all numbers to the
results/ folder (change with --out).
"""

import argparse
import os
import sys

from labassist.calibration import analyse, parse_points, read_points
from labassist.report import summary_text, write_outputs


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # Windows: never crash on symbols like σ or ⚠ when output is redirected
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Calibration linearity and LOD/LOQ from (X, Y, SD) points.")
    p.add_argument("file", nargs="?", help="CSV or text file with columns X, Y, SD")
    p.add_argument("--points", help='points typed directly, e.g. "(0, 0.013, 0.0008), (20, 0.050, 0.0017)"')
    p.add_argument("--n", type=int, help="number of replicate readings behind each mean and SD")
    p.add_argument("--k", type=float, default=3.3, help="LOD factor: 3.3 (ICH Q2(R2), default) or 3 (IUPAC)")
    p.add_argument("--range", nargs=2, type=float, metavar=("LOW", "HIGH"),
                   help="fit only the points from LOW to HIGH concentration")
    p.add_argument("--no-auto-range", action="store_true",
                   help="do not suggest a narrower range when the full range is curved")
    p.add_argument("--conc-unit", default="", help="concentration unit, e.g. nM")
    p.add_argument("--signal-unit", default="", help="response unit, e.g. uA")
    p.add_argument("--out", default="results", help="output folder (default: results)")
    a = p.parse_args()

    if bool(a.file) == bool(a.points):
        p.error("give either a file or --points")
    points, notes = read_points(a.file) if a.file else parse_points(a.points)
    results = analyse(points, n_replicates=a.n, k=a.k, x_range=tuple(a.range) if a.range else None,
                      auto_range=not a.no_auto_range, conc_unit=a.conc_unit, signal_unit=a.signal_unit,
                      reading_notes=notes)

    stem = os.path.splitext(os.path.basename(a.file))[0] if a.file else "points"
    if a.range:
        stem += f"_{a.range[0]:g}-{a.range[1]:g}"
    paths = write_outputs(results, a.out, stem, a.file or "typed points")
    print(summary_text(results, files=paths))


if __name__ == "__main__":
    main()
