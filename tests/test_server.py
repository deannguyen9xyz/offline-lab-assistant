"""End-to-end test of the MCP server through a real MCP client, the way LM Studio talks to it.

Run from the repository folder:  python -m pytest
"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server.py")


def call_server(calls, results_dir):
    """Start server.py, list its tools, run the given tool calls, return (tool names, texts)."""
    async def main():
        params = StdioServerParameters(command=sys.executable, args=[SERVER],
                                       env={"LAB_RESULTS_DIR": str(results_dir)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = [t.name for t in (await session.list_tools()).tools]
                texts = []
                for tool, args in calls:
                    result = await session.call_tool(tool, args)
                    assert not result.is_error, result.content[0].text
                    texts.append(result.content[0].text)
                return names, texts
    return asyncio.run(main())


def test_tools_and_typical_requests(tmp_path):
    names, (files, typed, by_name, missing) = call_server([
        ("list_data_files", {}),
        # points typed in the chat, picomolar, six levels
        ("analyse_calibration", {"data": "(0, 0.021, 0.0011), (1, 0.034, 0.0013), (2, 0.046, 0.0012), "
                                         "(5, 0.088, 0.0019), (10, 0.152, 0.0024), (20, 0.271, 0.0035)",
                                 "n_replicates": 3, "conc_unit": "pM"}),
        # a file from the data folder, named without the extension
        ("analyse_calibration", {"data": "example_points", "n_replicates": 3, "conc_unit": "nM"}),
        ("analyse_calibration", {"data": "no_such_run.csv"}),
    ], tmp_path)

    assert set(names) == {"analyse_calibration", "list_data_files"}
    assert "example_points.csv" in files

    assert "DATA: 6 points read (X, Y, SD): (0, 0.021, 0.0011)" in typed
    assert "ASK THE USER" not in typed and "FILES:" in typed  # n was given, so nothing to ask
    assert "LOD" in typed and "pM" in typed
    assert any(f.suffix == ".png" for f in tmp_path.iterdir())

    assert "Range used: 0–100 nM (suggested)" in by_name
    reports = [f for f in tmp_path.iterdir() if f.name.endswith("_report.md")]
    assert len(reports) == 2  # each analysis keeps its own files, even within the same second
    assert "Could not analyse the data: No file 'no_such_run.csv'" in missing
    assert "example_points.csv" in missing  # tells the model which files exist


def test_small_and_unusual_inputs(tmp_path):
    _, (three, brackets, falling, bad) = call_server([
        ("analyse_calibration", {"data": "(0, 0.013, 0.0008), (20, 0.050, 0.0017), (40, 0.106, 0.0021)"}),
        ("analyse_calibration", {"data": "[[0, 0.013, 0.0008], [20, 0.050, 0.0017], [40, 0.106, 0.0021], "
                                         "[60, 0.150, 0.0024], [80, 0.195, 0.0027]]", "k": 3}),
        # signal-off sensor: the response falls as concentration rises
        ("analyse_calibration", {"data": "(0, 1.00, 0.01), (1e-12, 0.91, 0.01), (2e-12, 0.80, 0.012), "
                                         "(3e-12, 0.71, 0.012), (4e-12, 0.60, 0.015)", "conc_unit": "M"}),
        ("analyse_calibration", {"data": "(0, 0.013), (20, 0.050), (40, 0.106)"}),
    ], tmp_path)

    assert "3 of 3 points" in three and "too few for Mandel" in three and "rough estimate" in three
    assert "ASK THE USER" in three
    assert "5 points" in brackets and "k = 3)" in brackets
    assert "y = -" in falling or "y = −" in falling or "−0." in falling
    assert "Could not analyse the data" in bad and "X, Y and SD" in bad
