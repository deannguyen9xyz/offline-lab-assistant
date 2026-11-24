"""Measure how reliably the model loaded in LM Studio uses Lina's calibration tool.

Run it on the computer with LM Studio:
  1. Load the model in LM Studio and start the local server
     (Developer tab -> Start server; the default address is http://localhost:1234).
  2. In this folder:   .venv\\Scripts\\python check_model.py
     Options:  --repeats 5   --temperature 0.5   --model <model id>   --url http://localhost:1234/v1

The script sends the same system prompt and tool descriptions that the chat uses, asks five
test questions several times, runs the real tool whenever the model calls it, and checks:
  - Does the model call the tool when it should, and not when it should not?
  - Are the user's numbers copied into the tool call exactly?
  - Does the final answer quote the tool's LOD values exactly and ask what it should ask?
It prints a score table and saves every request and answer to results/model_check_<time>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "results"
os.environ.setdefault("LAB_RESULTS_DIR", str(LOG_DIR / "model_check"))

import server as lab  # noqa: E402  same tools and descriptions as LM Studio sees
from labassist.calibration import parse_points  # noqa: E402

PM_POINTS = ("(0, 0.021, 0.0011), (1, 0.034, 0.0013), (2, 0.046, 0.0012), (5, 0.088, 0.0019), "
             "(10, 0.152, 0.0024), (20, 0.271, 0.0035)")
TABLE = ("conc (nM)  signal  SD\n0  0.013  0.0008\n20  0.050  0.0017\n40  0.106  0.0021\n"
         "60  0.150  0.0024\n80  0.195  0.0027")
TABLE_POINTS = "(0, 0.013, 0.0008), (20, 0.050, 0.0017), (40, 0.106, 0.0021), (60, 0.150, 0.0024), (80, 0.195, 0.0027)"
TOOL_ARGS = ("data", "n_replicates", "conc_unit", "signal_unit", "k", "range_low", "range_high")


def system_prompt() -> str:
    text = (ROOT / "prompts" / "lina_system_prompt.md").read_text(encoding="utf-8")
    return re.search(r"```text\n(.*?)```", text, re.S).group(1).strip()


def tool_definitions() -> list[dict]:
    tools = asyncio.run(lab.server.list_tools())
    return [{"type": "function",
             "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
            for t in tools]


def cases() -> list[dict]:
    first_question = f"Calibration of my DNA sensor, concentrations in pM: {PM_POINTS}. What are the LOD and LOQ?"
    earlier_result = lab.analyse_calibration(data=PM_POINTS, conc_unit="pM")
    history = [
        {"role": "user", "content": first_question},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "analyse_calibration",
                         "arguments": json.dumps({"data": PM_POINTS, "conc_unit": "pM"})}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": earlier_result},
        {"role": "assistant", "content": "With k = 3.3 the LOD is 0.289 pM from the blank SD and 0.786 pM from "
                                         "the line. How many readings are behind each mean and SD?"},
    ]
    return [
        {"name": "typed points (pM)", "user": first_question, "tool": True, "points": PM_POINTS,
         "unit": "pm", "n": None, "round_trip": True, "ask_n": True},
        {"name": "data file with n", "tool": True, "file": "example_points", "unit": "nm", "n": 3,
         "round_trip": True,
         "user": "Please analyse example_points.csv. Each point is the mean of 3 readings; concentrations in nM."},
        {"name": "pasted table", "user": f"Is this calibration linear?\n{TABLE}", "tool": True,
         "points": TABLE_POINTS, "unit": "nm"},
        {"name": "general question", "tool": False,
         "user": "In two sentences: what is the difference between LOD and LOQ?"},
        {"name": "follow-up, k = 3", "history": history, "user": "Please redo it with k = 3.", "tool": True,
         "points": PM_POINTS, "k": 3.0},
    ]


# ---------------------------------------------------------------- talking to LM Studio

def get_json(url: str, timeout: float = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def pick_model(url: str, wanted: str | None) -> str:
    if wanted:
        return wanted
    base = url.rsplit("/v1", 1)[0]
    for endpoint in ("/api/v1/models", "/api/v0/models"):  # LM Studio's own lists say what is loaded
        try:
            data = get_json(base + endpoint)
        except (urllib.error.URLError, ValueError):
            continue
        if "models" in data:  # LM Studio 0.4 and newer
            loaded = [inst.get("id") or m.get("key") for m in data["models"] if m.get("type") in ("llm", "vlm")
                      for inst in (m.get("loaded_instances") or [])]
        else:  # LM Studio 0.3
            loaded = [m["id"] for m in data.get("data", [])
                      if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm")]
        if len(loaded) == 1:
            return loaded[0]
        if len(loaded) > 1:
            sys.exit(f"Several models are loaded ({', '.join(loaded)}). Choose one with --model.")
    try:
        ids = [m["id"] for m in get_json(url + "/models").get("data", []) if "embed" not in m["id"].lower()]
    except urllib.error.URLError:
        sys.exit(f"Cannot reach LM Studio at {url}. Load the model and start the server "
                 "(Developer tab -> Start server), then run this again.")
    if len(ids) == 1:
        return ids[0]
    sys.exit("Cannot tell which model to test. Run again with --model and one of: " + ", ".join(ids))


def chat(ctx: dict, messages: list[dict]) -> tuple[dict, str | None, float]:
    body = {"model": ctx["model"], "messages": messages, "tools": ctx["tools"],
            "max_tokens": ctx["max_tokens"], "stream": False}
    if ctx["temperature"] is not None:
        body["temperature"] = ctx["temperature"]
    request = urllib.request.Request(ctx["url"] + "/chat/completions", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    start = time.time()
    with urllib.request.urlopen(request, timeout=ctx["timeout"]) as response:
        data = json.load(response)
    choice = data["choices"][0]
    return choice["message"], choice.get("finish_reason"), time.time() - start


def first_tool_call(message: dict) -> tuple[str | None, dict, str | None]:
    calls = message.get("tool_calls") or []
    if not calls:
        return None, {}, None
    fn = calls[0].get("function", {})
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_unreadable": args}
    return fn.get("name"), args, calls[0].get("id") or "call_0"


def clean_args(args: dict) -> dict:
    out = {k: v for k, v in args.items() if k in TOOL_ARGS and v not in (None, "")}
    if "n_replicates" in out:
        out["n_replicates"] = int(float(out["n_replicates"]))
    for key in ("k", "range_low", "range_high"):
        if key in out:
            out[key] = float(out[key])
    return out


# ---------------------------------------------------------------- one test run

def same_points(text_a: str, text_b: str) -> bool:
    a, _ = parse_points(text_a)
    b, _ = parse_points(text_b)
    return len(a) == len(b) and all(np.array_equal(u, v) for u, v in ((a.x, b.x), (a.y, b.y), (a.sd, b.sd)))


def run_case(case: dict, ctx: dict) -> dict:
    messages = [{"role": "system", "content": ctx["prompt"]}, *case.get("history", []),
                {"role": "user", "content": case["user"]}]
    message, finish, seconds = chat(ctx, messages)
    log = {"case": case["name"], "reply": message, "finish_reason": finish, "seconds": round(seconds, 1)}
    problems, notes = [], []
    name, args, call_id = first_tool_call(message)

    if not case["tool"]:
        if name:
            problems.append(f"called {name} for a general question")
        elif not (message.get("content") or "").strip():
            problems.append("empty answer")
        return {**log, "problems": problems, "notes": notes}

    if name != "analyse_calibration":
        reason = f"called {name} instead" if name else "did not call the tool"
        if finish == "length":
            reason += " (ran out of tokens, possibly a thinking loop)"
        return {**log, "problems": [reason], "notes": notes}

    if not isinstance(args.get("data"), str):
        problems.append("'data' was not sent as text (LM Studio would reject this call)")
        return {**log, "problems": problems, "notes": notes}
    data = args["data"]
    if "points" in case:
        try:
            if not same_points(data, case["points"]):
                problems.append("numbers in the tool call differ from the user's")
        except ValueError as exc:
            problems.append(f"tool call data could not be read: {exc}")
    if "file" in case and case["file"] not in data.lower():
        problems.append(f"file name not passed (data = {data!r})")
    if "k" in case and float(args.get("k") or 3.3) != case["k"]:
        problems.append(f"k = {args.get('k')} (expected {case['k']:g})")
    unit = str(args.get("conc_unit") or "").strip().lower()
    if case.get("unit") and unit != case["unit"]:
        notes.append(f"conc_unit = {args.get('conc_unit')!r}")
    if "n" in case and case["n"] is not None and args.get("n_replicates") not in (case["n"], str(case["n"])):
        notes.append(f"n_replicates = {args.get('n_replicates')!r} (expected {case['n']})")
    if "n" in case and case["n"] is None and args.get("n_replicates") not in (None, "", 0):
        notes.append(f"n_replicates = {args.get('n_replicates')!r} although the user did not say")

    if case.get("round_trip") and not problems:
        result = lab.analyse_calibration(**clean_args(args))
        messages += [{"role": "assistant", "content": message.get("content") or "",
                      "tool_calls": [{"id": call_id, "type": "function",
                                      "function": {"name": name, "arguments": json.dumps(args)}}]},
                     {"role": "tool", "tool_call_id": call_id, "content": result}]
        final, finish2, seconds2 = chat(ctx, messages)
        text = final.get("content") or ""
        log.update({"tool_result": result, "final_reply": final, "seconds": round(seconds + seconds2, 1)})
        lods = re.findall(r"^\s+(?:A|B), [^:]*: LOD (\S+) ", result, re.M)
        missing = [v for v in lods if v not in text]
        if missing:
            problems.append("answer does not quote the LOD value(s) " + ", ".join(missing) + " exactly")
        if finish2 == "length":
            problems.append("final answer cut off (ran out of tokens)")
        if final.get("tool_calls"):
            notes.append("called a tool again instead of answering")
        if "_report.md" not in text:
            notes.append("no report path in the answer")
        if case.get("ask_n") and not ("?" in text and re.search(r"reading|replicate|measurement|repeat", text, re.I)):
            notes.append("did not ask for the number of readings")
    return {**log, "problems": problems, "notes": notes}


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles: never crash on symbols like σ or ⚠
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description="Check how well the loaded LM Studio model uses Lina's tool.")
    p.add_argument("--url", default="http://localhost:1234/v1", help="LM Studio server address")
    p.add_argument("--model", help="model id (default: the model loaded in LM Studio)")
    p.add_argument("--repeats", type=int, default=3, help="runs per test (default 3)")
    p.add_argument("--temperature", type=float, help="sampling temperature (default: the model's setting)")
    p.add_argument("--max-tokens", type=int, default=3000, help="answer length limit (default 3000)")
    p.add_argument("--timeout", type=float, default=600, help="seconds to wait for one answer (default 600)")
    a = p.parse_args(argv)

    url = a.url.rstrip("/")
    ctx = {"url": url, "model": pick_model(url, a.model), "tools": tool_definitions(), "prompt": system_prompt(),
           "temperature": a.temperature, "max_tokens": a.max_tokens, "timeout": a.timeout}
    temp = "model default" if a.temperature is None else f"{a.temperature:g}"
    print(f"Testing {ctx['model']} (temperature: {temp}), {a.repeats} run(s) per test. "
          "This can take several minutes.\n")

    runs, all_cases = [], cases()
    for case in all_cases:
        for i in range(a.repeats):
            print(f"  {case['name']}, run {i + 1} ... ", end="", flush=True)
            try:
                record = run_case(case, ctx)
            except urllib.error.URLError as exc:
                record = {"case": case["name"], "problems": [f"request failed: {exc}"], "notes": [], "seconds": 0}
            except Exception as exc:  # an unexpected reply from the model must not stop the whole check
                record = {"case": case["name"], "problems": [f"unexpected reply ({type(exc).__name__}: {exc})"],
                          "notes": [], "seconds": 0}
            runs.append(record)
            print("ok" if not record["problems"] else "FAILED: " + "; ".join(record["problems"]))

    print(f"\n{'Test':<22}{'Passed':>8}{'Avg time':>10}  Notes")
    total = passed = 0
    for case in all_cases:
        rec = [r for r in runs if r["case"] == case["name"]]
        ok = sum(not r["problems"] for r in rec)
        total, passed = total + len(rec), passed + ok
        avg = sum(r["seconds"] for r in rec) / len(rec)
        remarks = sorted({n for r in rec for n in r["notes"]} | {q for r in rec for q in r["problems"]})
        print(f"{case['name']:<22}{f'{ok}/{len(rec)}':>8}{avg:>9.1f}s  {'; '.join(remarks)[:120]}")
    share = passed / total
    print(f"\nOverall: {passed}/{total} runs passed ({share:.0%}).")
    if share >= 0.9:
        print("Good: this model and these settings work well with Lina.")
    elif share >= 0.7:
        print("Usable, but check Lina's numbers against the report. Try --temperature 0.3, or switch thinking "
              "on or off in the model settings, and compare.")
    else:
        print("Not reliable with these settings. Try --temperature 0.3, switch thinking on or off, or another "
              "4-bit build of the model, and run the check again.")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"model_check_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    log_path.write_text(json.dumps({"model": ctx["model"], "temperature": a.temperature, "repeats": a.repeats,
                                    "passed": passed, "total": total, "runs": runs}, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"Full log: {log_path}")
    return 0 if share >= 0.9 else 1


if __name__ == "__main__":
    sys.exit(main())
