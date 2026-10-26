# Nightshift — PLAN

A pipeline that takes GitHub issues, works through as many as it safely can overnight,
and leaves a morning digest for a human to review.

Goals (in order): GitHub showcase, learning, fun. Cost is not a concern while building;
revisit when using it on real projects.

Status legend: **Decided** = settled by the owner. **Hypothesis** = a starting guess to
be tested or tuned with data, not a commitment.

## Decided

- Issues live on GitHub; Nightshift talks to GitHub via `gh`.
- The pipeline's effort scales with issue complexity: depth of planning, testing, time
  and money limits are set per issue in the plan phase, not globally.
- The plan phase decides whether a human is needed (unclear, risky, important changes).
- Jev (TypeSafe's decision model) answers constrained questions (Choice / Score / Yes-No).
  Coding agents do the actual work.
- Default executor is Claude Code; simpler issues go to cheaper/faster models. Not tied
  to Anthropic: any model that fits the issue best can be used, possibly chosen by Jev.
- Every run is logged (executor, tier, outcome, cost, time) so routing can be learned later.
- A watchdog checks progress during a run; a pre-PR check runs before opening the PR.
- Auto-merge into a `dev` branch is allowed only for changes that clear several gates.
  Humans promote `dev` → `main`. Everything auto-merged must be revertable, ideally
  automatically if it breaks the build.
- One environment for now (dev). No separate predev/prod.
- Morning digest summarising what happened.
- Agents may propose new issues they discover, and propose splits of oversized issues.
  Both need human approval at first.
- Unclear issues get refined before being escalated: an agent reads the code and docs and
  improves the issue (see Refinement).
- Work backlog data is employer data: not used in public repos or third-party APIs.
- Testbed is a separate repo: a recipe app (see Testbed).

## Pipeline (outline)

```
issue → triage → plan → [needs-human?] → implement → verify → pre-PR check
      → PR → merge gates → merge to `dev` → post-merge test → revert on failure → digest
```

- **Triage (Jev):** tier, clarity, risk, duplicate?, needs running-app testing?
  Triage also ends in a disposition, one of: proceed, refine (improve the issue first),
  ask human, recheck, or suggest won't-do. Nightshift only suggests the last two: a human
  confirms before anything is closed. Examples: an issue that looks already done or
  obsolete (recipe #9) is `recheck`; a duplicate is `recheck` pointing at the original.
- **Business decisions:** when an issue hinges on a product or business choice the issue
  doesn't make (a new name, which diets, which model, pricing), Nightshift asks the human
  that specific question instead of guessing. Jev Yes/No: "does this need a decision the
  issue doesn't make?" Many raw one-line issues are expected to end up here, which is fine.
- **Plan:** produces a per-issue "run contract": tier, executor, test strategy, scope
  (files the change may touch), time/cost limits, stop conditions.
- **Implement / verify:** coding agent in its own git worktree and branch; tests chosen
  by the contract.
- **Watchdog (Jev):** is the agent stuck or drifting out of scope? Abort or retry.
- **Pre-PR check (Jev):** does the diff address the issue? any red flags?
- **Merge gates:** tests green, diff in scope, independent review agrees, Jev score above
  a threshold, not flagged risky. If any gate fails the PR stays open for the human.
- **Safety (applies to the repos Nightshift works on, such as the recipe app; Nightshift's
  own repo is unprotected):** branch protection on `main` (PR + passing CI, no force-push
  or deletion) and `dev` (passing CI, no force-push or deletion); tag before each batch
  for easy rollback. Caveat: while Nightshift runs with the owner's personal token, GitHub
  cannot stop it opening and merging a PR into `main`, because the owner may bypass.
  Before auto-merge runs overnight, give Nightshift its own identity (a machine user or
  GitHub App) and require an approving review on `main`, so only the human can promote
  `dev` → `main`.
- **Digest:** merged (with revert commands), open PRs and why, needs-human questions,
  failures/reverts, proposed issues and splits awaiting approval.

## Refinement (outline; details are hypotheses)

When triage finds an issue unclear, a refine step runs before asking a human:

1. An agent reads the relevant code, tests and docs and drafts what is missing: likely
   affected files, a repro, acceptance criteria, edge cases.
2. It keeps what it found in the code (facts) separate from what it is guessing
   (assumptions), and turns anything it cannot infer into specific questions.
3. The original issue text is never overwritten. The result is a comment (or a proposed
   edit) marked `ns:refined`.
4. Jev re-scores clarity on the refined version. If it is now clear, the issue continues;
   if not, it goes to `ns:needs-human` with the questions.

To measure: clarity before vs after, and whether refined issues succeed more often than
raw ones. Risk to watch: the agent inventing requirements that sound plausible.

## Hypotheses to test (do not treat as fixed)

- Tier names and what each tier means (trivial / small / medium / large).
- Time and cost limits per tier. Start with generous placeholders, tighten from run logs.
- Which model suits which tier (haiku / sonnet / opus). Start with a simple table;
  let run logs show if it is wrong. Jev choosing the executor only after there is data.
- Cross-model review (a different model reviews the diff). Add Codex as an executor/reviewer
  when the basics work.
- Jev thresholds for "high confidence". Tune using the eval, not guesses.
- Splitting `risky` into per-category questions (schema, auth, deps, deploy).
- Proposed-issue and split rules: caps, depth limits, auto-approval later.
- Manual running-app testing by a browser agent for some tiers.
- Python for the orchestrator (not final).
- Hosting: not the laptop overnight. Build and try it locally first; the orchestrator is a
  CLI that runs from any machine with git, `gh` and the agent CLI. First scheduled home:
  a GitHub Actions cron in the target repo (nothing to keep on; GitHub is already the
  input and output; state kept in labels, comments and a data branch). Alternatives if
  Actions limits bite: a Railway cron service or the Pi. Unattended runs need an API key
  stored as a repo secret (per-token billing), to be checked against current terms.

## Jev notes

- Weights are fixed; it does not learn. Past issues and outcomes can be included in its
  input to give it context (retrieval).
- Spike done 2026-10-21 (code in `spike/`): direct route `POST https://api.typesafe.ai/v1/systemone`
  with a key from console.typesafe.ai works. `jev-latest` resolved to `jev-1.13.0`.
  Latency about 250–350 ms per call, ~600 input tokens per call, so negligible cost.
  Score levels are 0-based. On 5 hand-labeled issues: tier matched 3/5 (misses were
  arguable labels, one with low confidence), clarity 4/5, risk 4/5. The one real miss:
  an issue with a DB migration got risk 0.32. Five issues is too few to conclude anything.
- Keep Jev behind a small interface (`ask(state, questions)`) so it can be swapped.

## Testbed

A recipe app in its own repo: enter what you have in stock, get 2/3/4 recipe options and
a shopping list. Recipes are LLM-generated. Built as a simple v1 in a separate chat;
Nightshift takes over from there.

- Keep tests deterministic by putting the LLM behind a client that tests can fake.
- Issues are created on GitHub, across sizes, with a few vague, risky and duplicate ones,
  plus the expected labels kept privately for evals.
- Tag the v1 commit so evals can be re-run from the same starting point.

## Labels (proposal, to refine)

Two kinds, so humans and Nightshift don't overwrite each other:

- **Suggested by Nightshift (Jev), changeable by a human:** `priority:`, `type:`
  (bug / feature / chore), `size:`, `risk:`, `clarity:`. Nightshift only fills labels that
  are empty and never overwrites one a human set.
- **Learning from corrections (Decided):** every human change is logged and fed back as
  examples in Jev's input for similar issues, and used to tune thresholds. Jev's weights
  don't change; the "learning" is retrieval plus tuning.
- **State:** `ns:ready`, `ns:needs-human`, `ns:proposed`, `ns:in-progress`.

`size` and `complexity` overlap, so start with `size` only and add `complexity` if the
data shows they differ. For evals, labels on the issue are ground truth and are not
included in the text Jev sees.

## Build order

0. Jev spike. **Done.**
0.5. **Groundwork (done 2026-10-21):** uv project with tests, ruff and CI; `Decider`
   interface + Jev client; triage with split risk questions; `gh` label layer that never
   overwrites human labels; SQLite log with correction detection; eval harness and CLI.
   First eval on the 5 spike issues: clarity 5/5, type 5/5, risk 4/5 (the migration issue
   is now caught), size 3/5. Too few cases to conclude anything.
1. Recipe app v1 (separate chat) and ~30 GitHub issues with expected labels.
   **In progress (2026-10-21):** v1.0 is at github.com/qwillemse/recipe. Owner's 20 issues are
   created there (titles only, unlabeled). 10 more are drafted in `evals/recipe_issues.json`
   (not yet on GitHub), together with DRAFT expected labels for all 30 for the owner to
   correct. First run against those draft labels: clarity 27/30, risk 28/30, type 27/30,
   size 14/30 (most size misses are one step off, with low confidence). Provisional until
   the labels are corrected. Added a `research` type for investigation issues.
2. Triage-only eval: how well does Jev match the labels?
3. One simple tier end to end: issue in, draft PR out.
4. Run contract, limits, watchdog, pre-PR check.
5. Merge gates, auto-merge to `dev`, revert, overnight loop, digest.
6. Run logs → more executors, cross-model review, learned routing.
7. Issue refinement, proposed issues and splits (human-approved).
8. Running-app testing; README and write-up.

## Prior art (checked 2026-10-21)

Commercial and open-source tools do parts of this: GitHub Copilot coding agent, Devin,
Google Jules, Ona (scheduled backlog runs), Tembo, Continue, Warp, OpenHands,
microsoft/apm issue autopilot. None found that combines per-issue dynamic contracts,
Jev-style calibrated gates, and overnight auto-merge with automatic revert. Unclear how
complete that check is; look closer before claiming novelty.

## Not doing (for now)

- Merging to `main`, ever.
- Separate predev/prod environments.
- Running unapproved proposed issues or unapproved splits.
- Using work backlog data.
- Money/productization.
