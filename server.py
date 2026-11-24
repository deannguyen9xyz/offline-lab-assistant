"""MCP server that lets a local model in LM Studio run the calibration analysis.

LM Studio starts this file (see docs/setup.md). The model can call:
    analyse_calibration  linear range, sensitivity, LOD/LOQ for (X, Y, SD) points
    list_data_files      the data files the tool can open

Data files are read from the data/ folder next to this file (or the folder in
the LAB_DATA_DIR environment variable). Reports and figures are written to
results/ (or LAB_RESULTS_DIR). Nothing is sent over the network.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from pydantic import Field

from labassist.calibration import POINT_GROUP_START, analyse, parse_points, read_points
from labassist.report import summary_text, write_outputs

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LAB_DATA_DIR") or ROOT / "data").expanduser()
RESULTS_DIR = Path(os.environ.get("LAB_RESULTS_DIR") or ROOT / "results").expanduser()
DATA_SUFFIXES = (".csv", ".txt", ".tsv")
FILE_LIKE = re.compile(r"[A-Za-z]{2,}|[/\\]|\.(csv|txt|tsv)$", re.IGNORECASE)

server = MCPServer(
    "lab-assistant",
    instructions=("Calibration analysis for analytical chemistry. analyse_calibration computes the "
                  "linear range, calibration line, sensitivity, LOD and LOQ from (X, Y, SD) points or a "
                  "data file; list_data_files shows the available files. Never compute these numbers "
                  "yourself."),
)


# ---------------------------------------------------------------- helpers

def _data_files() -> list[Path]:
    if not DATA_DIR.is_dir():
        return []
    return sorted(f for f in DATA_DIR.iterdir() if f.is_file() and f.suffix.lower() in DATA_SUFFIXES)


def _find_file(name: str) -> Path | None:
    path = Path(name).expanduser()
    candidates = [path] if path.is_absolute() else [DATA_DIR / path, ROOT / path]
    candidates += [c.with_suffix(s) for c in list(candidates) if not c.suffix for s in DATA_SUFFIXES]
    for c in candidates:
        if c.is_file():
            return c
    wanted = {name.lower(), *(name.lower() + s for s in DATA_SUFFIXES)}
    return next((f for f in _data_files() if f.name.lower() in wanted), None)


def _load(data: str):
    """Return (points, notes, source description, output file stem)."""
    text = (data or "").strip()
    if not text:
        raise ValueError("No data given. Give a file name from the data folder, or the points as "
                         "(X, Y, SD) groups, e.g. (0, 0.013, 0.0008), (20, 0.050, 0.0017).")
    if "\n" not in text and not POINT_GROUP_START.search(text) and FILE_LIKE.search(text):
        path = _find_file(text)
        if path is None:
            names = ", ".join(f.name for f in _data_files()) or "none"
            raise FileNotFoundError(f"No file '{text}' in the data folder ({DATA_DIR}). Files there: {names}.")
        points, notes = read_points(str(path))
        return points, notes, str(path), path.stem
    points, notes = parse_points(text)
    return points, notes, "points typed in the chat", "points"


def _unique_stem(stem: str) -> str:
    """Add date and time so every analysis keeps its own report and figure."""
    base = f"{stem}_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    candidate, count = base, 1
    while (RESULTS_DIR / f"{candidate}_report.md").exists():
        count += 1
        candidate = f"{base}-{count}"
    return candidate


# ---------------------------------------------------------------- tools

@server.tool(structured_output=False)
def analyse_calibration(
    data: Annotated[str, Field(description=(
        "Either a data file name from the data folder (e.g. 'run1.csv'), or the calibration points exactly "
        "as the user gave them, written as (X, Y, SD) groups: '(0, 0.013, 0.0008), (20, 0.050, 0.0017)'. "
        "X = concentration, Y = mean response, SD = standard deviation of Y."))],
    n_replicates: Annotated[int | None, Field(description=(
        "Number of readings behind each mean and SD, only if the user said it."))] = None,
    conc_unit: Annotated[str, Field(description="Concentration unit used by the user, e.g. 'nM' or 'pM'.")] = "",
    signal_unit: Annotated[str, Field(description="Unit of the response, e.g. 'uA', if known.")] = "",
    k: Annotated[float, Field(description=(
        "LOD factor: 3.3 (ICH Q2(R2), default) or 3 (IUPAC 3-sigma, common in sensor papers)."))] = 3.3,
    range_low: Annotated[float | None, Field(description=(
        "Only if the user asks for a range: lowest concentration to include in the fit."))] = None,
    range_high: Annotated[float | None, Field(description=(
        "Only if the user asks for a range: highest concentration to include in the fit."))] = None,
) -> str:
    """Analyse a calibration curve: linear range, calibration line, sensitivity (slope), R², LOD and LOQ
    (from the blank SD and from the line), with statistical checks and plain-language conclusions.
    Saves a report and a figure. Use it whenever the user gives calibration points or a data file,
    or asks about linearity, sensitivity, LOD or LOQ. Never calculate these numbers yourself."""
    try:
        if not 0 < k <= 10:
            raise ValueError(f"k = {k} is not a sensible LOD factor; use 3.3 or 3.")
        points, notes, source, stem = _load(data)
        x_range = None
        if range_low is not None or range_high is not None:
            x_range = (float(points.x.min()) if range_low is None else range_low,
                       float(points.x.max()) if range_high is None else range_high)
            stem += f"_{x_range[0]:g}-{x_range[1]:g}"
        results = analyse(points, n_replicates=n_replicates, k=k, x_range=x_range,
                          conc_unit=conc_unit.strip(), signal_unit=signal_unit.strip(), reading_notes=notes)
        paths = write_outputs(results, str(RESULTS_DIR), _unique_stem(stem), source)
    except (ValueError, FileNotFoundError) as exc:
        return f"Could not analyse the data: {exc}"

    return "\n".join([
        f"Source: {source}",
        summary_text(results, files=paths, for_assistant=True),
        "Note for the assistant: answer in the order above, use these numbers exactly as written "
        "(do not recalculate or round them), and explain every CHECKS item.",
    ])


@server.tool(structured_output=False)
def list_data_files() -> str:
    """List the calibration data files (CSV or TXT) in the data folder that analyse_calibration can open."""
    files = _data_files()
    if not files:
        return f"The data folder ({DATA_DIR}) has no CSV or TXT files."
    lines = [f"Data folder: {DATA_DIR}"]
    for f in files:
        modified = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- {f.name} (modified {modified})")
    return "\n".join(lines)


if __name__ == "__main__":
    server.run()  # stdio transport, as LM Studio expects for a local server
