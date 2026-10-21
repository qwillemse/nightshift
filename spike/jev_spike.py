#!/usr/bin/env python3
"""Jev spike: send hand-written issues to Jev and print the answers.

Answers three questions: does our access route work, do the answers look sensible,
and what is the latency. Stdlib only.

    python3 spike/jev_spike.py --dry-run     # print the request, no network
    python3 spike/jev_spike.py               # needs TYPESAFE_API_KEY (env or .env)

Env:
    TYPESAFE_API_KEY   required for real calls
    JEV_BASE_URL       optional, default https://api.typesafe.ai (e.g. a LiteLLM proxy)
    JEV_MODEL          optional, default jev-latest
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIERS = {
    "trivial": "Typo, comment or one-line change; no behaviour decisions needed",
    "small": "Localised bug fix or tweak in one or two files",
    "medium": "Feature or change spanning several files, tests and maybe a migration",
    "large": "New subsystem, cross-cutting refactor, or anything touching auth/infra at scale",
}
CLARITY = [
    "Unusable: no concrete problem or outcome described",
    "Vague: problem named but scope and expected result are guesswork",
    "Partial: goal is clear but key acceptance details are missing",
    "Good: clear goal and mostly testable outcome",
    "Excellent: precise problem, repro or spec, and testable outcome",
]
QUESTIONS = {
    "tier": {
        "type": "choice",
        "instructions": "How large is the work needed to resolve this issue?",
        "criteria": TIERS,
    },
    "clarity": {
        "type": "score",
        "instructions": "How clearly is this issue specified for someone who has not seen the codebase?",
        "criteria": CLARITY,
    },
    "risky": {
        "type": "noul",
        "instructions": "Does resolving this touch authentication, secrets, database schema, "
        "dependencies or deployment, where a mistake is costly?",
    },
    "criteria_clear": {
        "type": "noul",
        "instructions": "Does the issue contain concrete acceptance criteria that a test could check?",
    },
    "needs_running_app": {
        "type": "noul",
        "instructions": "Would verifying the fix require running the app and exercising it manually, "
        "beyond unit tests?",
    },
}


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_request(issue, model):
    return {
        "state": {"title": issue["title"], "body": issue["body"]},
        "model": model,
        "questions": QUESTIONS,
    }


def call_jev(base_url, key, body, retries=3):
    url = base_url.rstrip("/") + "/v1/systemone"
    data = json.dumps(body).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp), (time.perf_counter() - t0) * 1000
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code in (429, 529) and attempt < retries:
                time.sleep(2**attempt)
                continue
            sys.exit(f"HTTP {e.code} from {url}\n{detail}")
        except urllib.error.URLError as e:
            sys.exit(f"Network error calling {url}: {e.reason}")


def expected_noul(exp):
    return "yes" if exp else "no"


def summarise(issue, resp, ms):
    a, exp = resp["answers"], issue["expected"]
    tier = a["tier"]
    print(f"\n== {issue['id']}  ({ms:.0f} ms, {resp['usage']['input_tokens']} in tokens)")
    tp = ", ".join(
        f"{k} {v:.2f}" for k, v in sorted(tier["probabilities"].items(), key=lambda x: -x[1])
    )
    mark = "OK " if tier["choice"] == exp["tier"] else "!! "
    print(
        f"  {mark}tier      -> {tier['choice']} (conf {tier['confidence']:.2f})  expected {exp['tier']}   [{tp}]"
    )
    cl = a["clarity"]
    # Score levels are 0-based (legend keys "0".."4"); issues.json labels clarity 1-5.
    want = exp["clarity"] - 1
    mark = "OK " if abs(cl["score"] - want) <= 0.75 else "!! "
    print(
        f"  {mark}clarity   -> score {cl['score']:.2f} on 0-4 (conf {cl['confidence']:.2f})  expected {want}"
    )
    for key in ("risky", "criteria_clear", "needs_running_app"):
        p = a[key]["noul"]
        guess = p >= 0.5
        mark = "OK " if guess == exp[key] else "!! "
        print(f"  {mark}{key:<17} -> p(yes)={p:.2f}  expected {expected_noul(exp[key])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print requests, no network")
    ap.add_argument("--raw", action="store_true", help="also print raw JSON responses")
    args = ap.parse_args()

    load_env()
    model = os.environ.get("JEV_MODEL", "jev-latest")
    base_url = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai")
    issues = json.loads((Path(__file__).parent / "issues.json").read_text())

    if args.dry_run:
        print(f"POST {base_url.rstrip('/')}/v1/systemone")
        print(json.dumps(build_request(issues[0], model), indent=2))
        print(f"\n({len(issues)} issues loaded, {len(QUESTIONS)} questions each)")
        return

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        sys.exit("TYPESAFE_API_KEY not set. Put it in .env (see .env.example) or export it.")

    latencies = []
    for issue in issues:
        resp, ms = call_jev(base_url, key, build_request(issue, model))
        latencies.append(ms)
        summarise(issue, resp, ms)
        if args.raw:
            print(json.dumps(resp, indent=2))
    latencies.sort()
    print(
        f"\nmodel: {resp['model']}   latency median {latencies[len(latencies) // 2]:.0f} ms, "
        f"max {latencies[-1]:.0f} ms over {len(latencies)} calls"
    )


if __name__ == "__main__":
    main()
