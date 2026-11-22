#!/usr/bin/env python3
"""review_eval.py - how well does one model review code? 25 samples with known answers.

The samples are in review_samples.json: the 6 basic-set reference solutions, one planted bug in each, and for
each of the 7 hard tasks one correct answer and the most nearly-correct wrong answer a model wrote in the hard
set (the hardest bugs to spot). Before any model is asked, every sample is run against the hidden checks of
code_test.py, so the true answer (PASS or FAIL) comes from the tests, not from the file.

The model gets the task and the code, not the tests, and must end with "VERDICT: PASS" or "VERDICT: FAIL".
Scored: right verdicts, bugs caught, false alarms (correct code marked FAIL), time and generation speed.

Usage:
  python3 review_eval.py --model review-qwen3.8 --out review.json
  python3 review_eval.py --url http://localhost:9704 --model review-qwen3.8 --out review-b60.json
  python3 review_eval.py --model research-qwen3.6 --brief --out brief.json   # verdict-only prompt
  python3 review_eval.py --check                                              # only re-check the samples
  python3 review_test.py --report review.json                                 # tables of a saved run

Needs code_test.py and review_test.py in the same folder. Thinking is off. Stdlib only.
The key is read from ~/locai/llama/api-key.txt unless you pass --key-file or set LLAMA_API_KEY.
"""
import argparse, importlib.util, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("review_test", os.path.join(HERE, "review_test.py"))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
ct, TASKS = rt.ct, rt.TASKS

BRIEF_SYSTEM = (
    "You are a strict senior code reviewer. You are given a task and a solution. Decide whether the solution "
    "fully meets every requirement of the task, including edge cases, error handling and performance limits. "
    "You cannot run the code. Check it carefully, but write only the problems you found, each in one sentence "
    "naming the input that fails; do not describe what is correct and do not restate the code. If you found "
    "no problem, write nothing before the verdict. Keep the reply under 120 words. "
    "End your reply with exactly one final line: VERDICT: PASS (it meets every requirement) "
    "or VERDICT: FAIL (any requirement is not met).")


def load_samples(path):
    samples = json.load(open(path))["samples"]
    for s in samples:
        t = TASKS[s["task"]]
        res, err = ct.run_checks(s["code"], t["checks"], t["helpers"])
        truth = "PASS" if all(res) else "FAIL"
        if truth != s.get("truth"):
            print("note: %s is %s against the current checks (file says %s)" % (s["id"], truth, s.get("truth")))
        s.update(truth=truth, passed=sum(res), total=len(res), error=err)
    return samples


def main():
    ap = argparse.ArgumentParser(description="One model reviews 25 code samples with known answers")
    ap.add_argument("--url", default="http://localhost:9700")
    ap.add_argument("--model", help="model name the server answers to")
    ap.add_argument("--out", default="review_eval.json")
    ap.add_argument("--key-file", default=os.path.expanduser("~/locai/llama/api-key.txt"))
    ap.add_argument("--samples", default=os.path.join(HERE, "review_samples.json"))
    ap.add_argument("--brief", action="store_true", help="ask for the problems and the verdict only")
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--check", action="store_true", help="only re-check the samples and print their true answers")
    a = ap.parse_args()

    samples = load_samples(a.samples)
    if a.check:
        for s in samples:
            print("%-22s %-4s %2d/%-2d checks  %s" % (s["id"], s["truth"], s["passed"], s["total"], s["source"]))
        print("%d samples, %d with a bug" % (len(samples), sum(s["truth"] == "FAIL" for s in samples)))
        return
    if not a.model:
        ap.error("--model is required (or use --check)")
    key = os.environ.get("LLAMA_API_KEY", "").strip()
    if not key:
        try: key = open(a.key_file).read().strip()
        except OSError as e: sys.exit("cannot read the key file (%s). Use --key-file or set LLAMA_API_KEY." % e)
    url = a.url.rstrip("/")
    system = BRIEF_SYSTEM if a.brief else rt.REVIEW_SYSTEM
    data = {"writer": "mixed (reference, planted bugs, saved hard-set answers)", "thinking": False, "seeded": True,
            "brief": a.brief, "url": url, "samples": samples, "reviews": []}
    print("== %s reviews %d samples (%d buggy) at %s" % (a.model, len(samples), sum(s["truth"] == "FAIL" for s in samples), url), flush=True)
    for s in samples:
        t = TASKS[s["task"]]
        user = "TASK:\n%s\n\nSOLUTION:\n```python\n%s\n```" % (t["prompt"], s["code"].strip())
        print("   %-22s " % s["id"], end="", flush=True)
        try:
            d, wall = rt.chat(url, key, a.model, system, user, False, a.max_tokens, a.timeout)
            text = d["choices"][0]["message"].get("content") or ""
            tm = d.get("timings") or {}
            v, tps, ntok = rt.verdict(text), tm.get("predicted_per_second", 0.0), tm.get("predicted_n", 0)
        except Exception as e:
            text, v, wall, tps, ntok = "error: %s" % e, "NONE", 0.0, 0.0, 0
        data["reviews"].append({"reviewer": a.model, "sample": s["id"], "verdict": v, "truth": s["truth"],
                                "wall_s": round(wall, 1), "gen_tokens": ntok, "tps": round(tps, 1), "review": text})
        json.dump(data, open(a.out, "w"), indent=1)
        print("%-4s (true %s) %-5s %6.1fs %5d tok %5.1f tok/s" % (v, s["truth"], "ok" if v == s["truth"] else "WRONG", wall, ntok, tps), flush=True)
    rt.report(data)
    print("\nresults saved to", a.out)


if __name__ == "__main__":
    main()
