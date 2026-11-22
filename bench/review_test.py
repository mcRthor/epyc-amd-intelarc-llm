#!/usr/bin/env python3
"""review_test.py - can the models review code? One model writes, every model reviews.

1. The writer model (default research-qwen3.6) writes code for each task in code_test.py.
2. The hidden checks run on that code (sandboxed), so the true answer is known: PASS or FAIL.
3. Every reviewer model (the writer too) gets the task and the code, but not the tests, and must
   end its reply with "VERDICT: PASS" or "VERDICT: FAIL".
4. Each verdict is scored against the true answer: bugs caught, false alarms, accuracy.

Usage:
  python3 review_test.py                                    # writer research-qwen3.6, every router model reviews
  python3 review_test.py --seeded                           # also review one planted-bug version of each task
  python3 review_test.py --writer fast-qwen --reviewers review-qwen3.8,research-qwen3.6
  python3 review_test.py --from-results code_test_X.json --writer agent-glimmer   # reuse saved code, skip writing
  python3 review_test.py --thinking                         # let reviewers think first (slower, often better)
  python3 review_test.py --check-seeded                     # confirm every planted bug fails the hidden checks
  python3 review_test.py --report review_test_X.json        # print the tables again from a saved run
  python3 review_test.py --rescore review_test_X.json       # re-check the reviewed code against the current checks

--seeded matters when the writer's code is mostly correct: without failing code, a reviewer that
always says PASS would look perfect. Needs code_test.py in the same folder (tasks and sandbox).
The key is read from ~/locai/llama/api-key.txt unless you pass --key-file or set LLAMA_API_KEY.
Uses only the Python standard library. The run swaps models on the router, so app requests wait meanwhile.
"""
import argparse, importlib.util, json, os, re, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("code_test", os.path.join(HERE, "code_test.py"))
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)
TASKS = {t["name"]: t for t in ct.TASKS}

REVIEW_SYSTEM = (
    "You are a strict senior code reviewer. You are given a task and a solution. Decide whether the solution "
    "fully meets every requirement of the task, including edge cases, error handling and performance limits. "
    "You cannot run the code. Explain briefly what you checked and any problem you found. "
    "End your reply with exactly one final line: VERDICT: PASS (it meets every requirement) "
    "or VERDICT: FAIL (any requirement is not met).")

# One subtle planted bug per task: a small edit to the reference solution (--seeded).
SEEDED = {
    "parse_duration": ("empty string returns 0 instead of raising ValueError",
                       lambda ref: ref.replace("if not t or not m:", "if not m:")),
    "merge_intervals": ("touching intervals such as (1, 3) and (3, 5) are not merged",
                        lambda ref: ref.replace("if out and a <= out[-1][1]:", "if out and a < out[-1][1]:")),
    "lru_cache": ("get() does not mark the key as recently used",
                  lambda ref: ref.replace("        self.d.move_to_end(key)\n        return self.d[key]", "        return self.d[key]")),
    "fix_moving_average": ("k > len(xs) is not rejected",
                           lambda ref: ref.replace("if k < 1 or k > len(xs):", "if k < 1:")),
    "topo_order": ("a task that depends on itself is not reported as a cycle",
                   lambda ref: ref.replace("for d in set(ds):", "for d in set(ds) - {t}:")),
    "roman": ("from_roman accepts non-canonical numerals such as IIII and IC",
              lambda ref: ref.replace(
                  "def from_roman(s):\n    if not _TABLE:\n        _TABLE.update({to_roman(i): i for i in range(1, 4000)})\n    if s not in _TABLE:\n        raise ValueError(s)\n    return _TABLE[s]",
                  "def from_roman(s):\n    m = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}\n"
                  "    if not s or any(c not in m for c in s):\n        raise ValueError(s)\n    total = 0\n"
                  "    for a, b in zip(s, s[1:] + ' '):\n        total += -m[a] if b != ' ' and m[a] < m[b] else m[a]\n"
                  "    if not 1 <= total <= 3999:\n        raise ValueError(s)\n    return total")),
}


def seeded_code(name):
    desc, edit = SEEDED[name]
    ref = TASKS[name]["reference"]
    code = edit(ref)
    if code == ref:
        raise RuntimeError("seeded edit did not apply for " + name)
    return desc, code


def chat(base, key, model, system, user, thinking, max_tokens, timeout):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if not thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(base + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return d, time.time() - t0


def verdict(text):
    found = re.findall(r"VERDICT:\s*\**\s*(PASS|FAIL)", text or "", re.I)
    return found[-1].upper() if found else "NONE"


def report(data):
    samples, reviews = data["samples"], data["reviews"]
    print("\nCODE UNDER REVIEW (writer: %s)" % data["writer"])
    for s in samples:
        print("  %-26s true %-4s  %s" % (s["id"], s["truth"], s.get("note", "%d/%d checks" % (s["passed"], s["total"]))))
    reviewers = list(dict.fromkeys(r["reviewer"] for r in reviews))
    w = max(len(s["id"]) for s in samples)
    print("\nVERDICTS (ok = matches the hidden tests)")
    print("%-17s " % "REVIEWER" + " ".join("%-*s" % (w, s["id"]) for s in samples))
    for rv in reviewers:
        cells = []
        for s in samples:
            r = next((x for x in reviews if x["reviewer"] == rv and x["sample"] == s["id"]), None)
            cells.append("%-*s" % (w, "-" if not r else "%s %s" % (r["verdict"], "ok" if r["verdict"] == s["truth"] else "WRONG")))
        print("%-17s " % rv + " ".join(cells))
    print("\nSUMMARY")
    print("%-17s %9s %10s %12s %11s %8s" % ("REVIEWER", "ACCURACY", "BUGS CAUGHT", "FALSE ALARMS", "NO VERDICT", "TIME"))
    for rv in reviewers:
        rs = [r for r in reviews if r["reviewer"] == rv]
        truth = {s["id"]: s["truth"] for s in samples}
        bad = [r for r in rs if truth[r["sample"]] == "FAIL"]
        good = [r for r in rs if truth[r["sample"]] == "PASS"]
        right = sum(r["verdict"] == truth[r["sample"]] for r in rs)
        print("%-17s %4d/%-4d %6d/%-4d %8d/%-4d %7d %8.0fs" % (
            rv, right, len(rs), sum(r["verdict"] == "FAIL" for r in bad), len(bad),
            sum(r["verdict"] == "FAIL" for r in good), len(good), sum(r["verdict"] == "NONE" for r in rs),
            sum(r["wall_s"] for r in rs)))


def main():
    ap = argparse.ArgumentParser(description="One model writes code, every model reviews it")
    ap.add_argument("--url", default="http://localhost:9700")
    ap.add_argument("--key-file", default=os.path.expanduser("~/locai/llama/api-key.txt"))
    ap.add_argument("--writer", default="research-qwen3.6")
    ap.add_argument("--reviewers", default="", help="comma-separated; default: every model the router lists except quick")
    ap.add_argument("--set", default="basic", choices=["basic", "hard", "all"], help="task set from code_test.py (default: basic)")
    ap.add_argument("--tasks", default="", help="comma-separated task names; default: all in the set")
    ap.add_argument("--seeded", action="store_true", help="also review one planted-bug version of each task")
    ap.add_argument("--from-results", default="", help="take the writer's code from a code_test.py results file")
    ap.add_argument("--thinking", action="store_true", help="let the models think first")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default="")
    ap.add_argument("--check-seeded", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--rescore", default="", help="re-run the current checks on the reviewed code, update the true answers, print the tables")
    a = ap.parse_args()

    if a.report:
        report(json.load(open(a.report))); return
    if a.rescore:
        data = json.load(open(a.rescore))
        for smp in data["samples"]:
            res, err = ct.run_checks(smp["code"], TASKS[smp["task"]]["checks"], TASKS[smp["task"]]["helpers"])
            truth = "PASS" if all(res) else "FAIL"
            if truth != smp["truth"]:
                print("%-26s true answer %s -> %s (%d/%d checks)" % (smp["id"], smp["truth"], truth, sum(res), len(res)))
            smp.update(truth=truth, passed=sum(res), total=len(res), error=err)
        truths = {smp["id"]: smp["truth"] for smp in data["samples"]}
        for r in data["reviews"]:
            r["truth"] = truths[r["sample"]]
        json.dump(data, open(a.rescore, "w"), indent=1)
        report(data); return
    tasks = [TASKS[n] for n in a.tasks.split(",")] if a.tasks else [t for t in TASKS.values() if a.set in ("all", t["level"])]
    if a.check_seeded:
        ok = True
        for t in [t for t in tasks if t["name"] in SEEDED]:
            desc, code = seeded_code(t["name"])
            r, err = ct.run_checks(code, t["checks"], t["helpers"])
            print("%-20s planted bug fails %d of %d checks  (%s)%s" % (t["name"], len(r) - sum(r), len(r), desc, " " + err if err else ""))
            ok &= not all(r)
        sys.exit(0 if ok else 1)

    key = os.environ.get("LLAMA_API_KEY", "").strip()
    if not key:
        try: key = open(a.key_file).read().strip()
        except OSError as e: sys.exit("cannot read the key file (%s). Use --key-file or set LLAMA_API_KEY." % e)
    base = a.url.rstrip("/")
    if a.reviewers:
        reviewers = [m.strip() for m in a.reviewers.split(",") if m.strip()]
    else:
        req = urllib.request.Request(base + "/v1/models", headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=30) as r:
            reviewers = [m["id"] for m in json.loads(r.read())["data"] if not m["id"].startswith("quick")]
    out = a.out or os.path.join(os.getcwd(), time.strftime("review_test_%Y%m%d-%H%M.json"))
    max_tokens = 16000 if a.thinking else 6000

    # 1. code under review
    saved = {}
    if a.from_results:
        saved = {r["task"]: r["answer"] for r in json.load(open(a.from_results)) if r.get("model") == a.writer and "answer" in r}
    samples = []
    print("== 1. %s writes the code%s" % (a.writer, " (from %s)" % a.from_results if saved else ""))
    for t in tasks:
        if t["name"] in saved:
            text = saved[t["name"]]
        else:
            d, wall = chat(base, key, a.writer, ct.SYSTEM, t["prompt"], a.thinking, 16000 if "glimmer" in a.writer else max_tokens, a.timeout)
            text = d["choices"][0]["message"].get("content") or ""
        code = ct.extract_code(text, t)
        r, err = ct.run_checks(code, t["checks"], t["helpers"])
        truth = "PASS" if all(r) else "FAIL"
        samples.append({"id": t["name"], "task": t["name"], "source": a.writer, "code": code, "truth": truth,
                        "passed": sum(r), "total": len(r), "error": err})
        print("   %-26s %2d/%-2d checks -> true %s" % (t["name"], sum(r), len(r), truth))
        if a.seeded and t["name"] in SEEDED:
            desc, code = seeded_code(t["name"])
            r, _ = ct.run_checks(code, t["checks"], t["helpers"])
            samples.append({"id": t["name"] + "+bug", "task": t["name"], "source": "planted bug", "code": code,
                            "truth": "PASS" if all(r) else "FAIL", "passed": sum(r), "total": len(r), "note": "planted: " + desc})
    n_fail = sum(s["truth"] == "FAIL" for s in samples)
    if n_fail < 2 and not a.seeded:
        print("   note: only %d failing sample(s); add --seeded so the test can show which reviewers catch bugs" % n_fail)

    # 2. reviews, one reviewer at a time (one model swap each)
    data = {"writer": a.writer, "thinking": a.thinking, "seeded": a.seeded, "samples": samples, "reviews": []}
    print("\n== 2. Reviews (%d samples, %d failing)" % (len(samples), n_fail))
    for rv in reviewers:
        mt = 16000 if "glimmer" in rv else max_tokens
        for s in samples:
            t = TASKS[s["task"]]
            user = "TASK:\n%s\n\nSOLUTION:\n```python\n%s\n```" % (t["prompt"], s["code"].strip())
            print("   %-17s %-26s " % (rv, s["id"]), end="", flush=True)
            try:
                d, wall = chat(base, key, rv, REVIEW_SYSTEM, user, a.thinking, mt, a.timeout)
                text = d["choices"][0]["message"].get("content") or ""
                v = verdict(text)
            except Exception as e:
                text, v, wall = "error: %s" % e, "NONE", 0.0
            data["reviews"].append({"reviewer": rv, "sample": s["id"], "verdict": v, "truth": s["truth"],
                                    "wall_s": round(wall, 1), "review": text})
            json.dump(data, open(out, "w"), indent=1)
            print("%-4s (true %s) %s %5.1fs" % (v, s["truth"], "ok" if v == s["truth"] else "WRONG", wall), flush=True)
    report(data)
    print("\nresults saved to", out)


if __name__ == "__main__":
    main()
