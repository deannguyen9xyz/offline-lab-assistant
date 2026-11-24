"""Tests for check_model.py against a fake LM Studio server (no real model needed).

A 'careful' fake model behaves as the system prompt asks; a 'sloppy' one rounds numbers.
The check must pass the first and catch the second.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import check_model


def fake_model_reply(body: dict, sloppy: bool) -> dict:
    messages = body["messages"]
    last = messages[-1]

    def tool_call(args):
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_9", "type": "function",
            "function": {"name": "analyse_calibration", "arguments": json.dumps(args)}}]}

    def groups(text):
        found = re.findall(r"\([^()]*\d[^()]*\)", text)
        if sloppy:  # rounds every number to two decimals: the kind of slip the check must catch
            found = [re.sub(r"\d+\.\d+", lambda m: f"{float(m.group()):.2f}", g) for g in found]
        return ", ".join(found)

    if last["role"] == "tool":
        lods = re.findall(r"LOD ([0-9][0-9.e+-]*) ", last["content"])
        quoted = lods if not sloppy else [f"{float(v):.1f}" for v in lods]
        text = "Results: LOD " + " and ".join(quoted) + "."
        if "ASK THE USER" in last["content"]:
            text += " How many readings are behind each mean and SD?"
        text += " Report: " + re.search(r"Report: (\S+)", last["content"]).group(1)
        return {"role": "assistant", "content": text, "tool_calls": []}

    user = last["content"]
    if "difference between LOD and LOQ" in user:
        return {"role": "assistant", "content": "LOD is the lowest detectable level; LOQ is the lowest level "
                                                "that can be quantified.", "tool_calls": []}
    if "redo it with k = 3" in user:
        return tool_call({"data": groups(messages[1]["content"]), "conc_unit": "pM", "k": 3})
    if "example_points.csv" in user:
        return tool_call({"data": "example_points.csv", "n_replicates": 3, "conc_unit": "nM"})
    if "Is this calibration linear?" in user:
        rows = [ln.split() for ln in user.splitlines()[2:]]
        return tool_call({"data": groups(", ".join(f"({', '.join(r)})" for r in rows)), "conc_unit": "nM"})
    return tool_call({"data": groups(user), "conc_unit": "pM"})


def serve(sloppy: bool):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, payload):
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # LM Studio 0.4 style list: the loaded model has a loaded instance
            if self.path == "/api/v1/models":
                self.send({"models": [{"type": "llm", "key": "other-model", "loaded_instances": []},
                                      {"type": "llm", "key": "fake-12b", "loaded_instances": [{"id": "fake-12b"}]}]})
            else:
                self.send({"data": [{"id": "other-model"}, {"id": "fake-12b"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["tools"][0]["function"]["name"] == "analyse_calibration"
            message = fake_model_reply(body, sloppy)
            self.send({"choices": [{"message": message, "finish_reason": "stop"}]})

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.mark.parametrize("sloppy", [False, True])
def test_check_model_scores_careful_and_sloppy_models(sloppy, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(check_model, "LOG_DIR", tmp_path)
    monkeypatch.setattr(check_model.lab, "RESULTS_DIR", tmp_path / "tool")
    httpd = serve(sloppy)
    try:
        code = check_model.main(["--url", f"http://127.0.0.1:{httpd.server_port}/v1", "--repeats", "1"])
    finally:
        httpd.shutdown()
    out = capsys.readouterr().out
    assert "Testing fake-12b" in out
    log = json.loads(next(tmp_path.glob("model_check_*.json")).read_text(encoding="utf-8"))
    if not sloppy:
        assert code == 0 and "Overall: 5/5" in out, out
    else:
        assert code == 1
        problems = " ".join(p for run in log["runs"] for p in run["problems"])
        assert "numbers in the tool call differ" in problems           # rounded input caught
        assert "does not quote the LOD value(s)" in problems           # rounded answer caught
        assert log["passed"] == 1  # only the general question has no numbers to round
