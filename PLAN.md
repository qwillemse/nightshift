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
  to Anthropic: any model that fits the issue best can be used.
- The coding model is chosen per issue from its difficulty and size and from the
  available budget: a better model for harder or larger issues, a cheaper one for easy ones.
- Different stages may use different models (triage, issue improvement and creation,
  implementation, testing, review, digest). They need not be the same model.
- Jev chooses the model from the start, on intuition (descriptions of each model and the
  issue, no history). Once run logs exist, past performance is added to its input so it
  chooses from experience. The static table stays as a baseline to compare Jev's picks to.
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

## Pipeline stages (Decided, owner's outline)

1. **Read issues:** improve, classify, ask questions, split into smaller issues, create new
   issues, decide which to implement.
2. **Implement:** chosen by priority, complexity, budget and so on; may create new issues.
3. **Test:** automatic testing, and for some issues manual-test instructions for a human
   (maybe agent-run manual testing later); may create new issues.
4. **Morning digest:** what changed, what needs testing, what issues need reviewing,
   questions for business decisions, new issues, what is still to do.

Rule for improving issues: **no silent inventions.** Improving an existing issue may add
what is found in the code and docs, tighten wording, and turn gaps into questions. It
must not quietly add features, requirements or acceptance criteria the owner did not ask
for; anything it would have to guess becomes a question, and added content is labeled as
inferred from code or docs. Nightshift **may suggest new things**, but as clearly marked
suggestions or proposed issues for the human to review (keep / don't keep / alter), never
as edits slipped into an existing issue.

## Pipeline detail (outline)

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

## Question loop (behaviour decided; mechanism is a hypothesis)

Most raw issues are expected to end up as questions for a human. That is fine as long as
the loop ends with an issue that is ready to implement on the next shift:

1. **Ask:** Nightshift improves the issue as far as it can (see Refinement) and asks the
   specific questions it cannot answer, including business decisions (naming, which diets,
   which model, pricing).
2. **Answer:** the human replies in the morning.
3. **Rewrite:** the next run folds the answers into the issue (What / Acceptance criteria /
   Notes template), Jev re-scores clarity, and if it is clear the issue becomes `ns:ready`.
4. **Implement:** the following shift picks it up.

**Question policy (Decided 2026-10-26):**

- No cap on the number of questions, and no cap on blocking ones. Asking beats guessing:
  when unsure whether a choice matters, ask. Many blocking questions on a very unclear
  issue is expected and fine.
- The owner can answer, skip, or push a question to later. A skipped non-blocking question
  proceeds with its stated default; a blocking one keeps the issue waiting.
- Obvious or unimportant choices (for example how to round a displayed number) Nightshift
  may make itself. They are listed as "decisions made" in the digest and the PR so they can
  be reverted.
- Nightshift may suggest additions it thinks are obvious, always as marked suggestions,
  never silently added to the issue.
- The digest therefore also lists decisions Nightshift made itself.

The morning digest carries three kinds of items the human can act on:

- **Questions** on vague or decision-dependent issues, with Nightshift's proposed rewrite.
- **Proposed issues** found while working (see issue discovery): keep / don't keep / alter.
- **Proposed splits and rechecks** (possible duplicates, obsolete or won't-do suggestions).

Nothing is closed or created without the human's choice. Mechanism to try first (hypothesis):
the digest is a GitHub issue per night with a checklist and one comment thread per item,
which also works from a phone. Alternatives: labels or reactions on each item.

## Refinement (outline; details are hypotheses)

**Trigger (implemented, thresholds are guesses):** triage returns disposition `refine` when
clarity is low or the model's confidence in the size is below 0.4. A refine-disposition
issue gets no model pick; it is improved first and triaged again, and only then is the
implementing model chosen (second Jev call, which sees the classification and budget).
On the 30 recipe cases, 18 were sent to refine (almost all because of low clarity) and 12
proceeded.

**Implemented as a dry run (2026-10-26):** `nightshift refine` gathers the repo's docs, file
tree and the files most related to the issue, asks a model for a structured result, and
prints a markdown draft without touching GitHub. The no-invention rule is enforced in code:
every acceptance criterion must quote the issue text, every code fact must quote a file the
model was shown, and anything whose quote cannot be found is dropped and listed. Ideas go
in a separate suggestions list. Jev then checks whether the improved text adds
requirements. Backends: Anthropic API, or the logged-in `claude` CLI.

**First real dry run (2026-10-26, Sonnet 5.5, recipe#2, #3, #9, x8):** every quote verified
(nothing dropped), questions were specific with business decisions first, #9 was correctly
flagged as possibly already done, and related issues (#1, #23) were cross-referenced.
Problems found: (1) context selection by keyword missed `generators/anthropic_gen.py` for
x8, so the model suggested adding a variety instruction that the real prompt already has;
(2) 3 to 4 blocking questions per issue is a lot to put on the owner; (3) Jev's invention
score looked noisy (0.46 on a mild case, 0.13 on another) and is not yet validated;
(4) cost per call is not reported yet. Fixes planned: let the model ask for files it needs
and re-run once, cap the number of blocking questions, validate the invention check on
deliberately inventive outputs, report tokens and cost.

**Fixes applied and re-run (2026-10-26):** the model can now ask for files it was not shown
and gets one more pass (so x8 saw the real prompt and no longer suggests adding a variety
instruction that already exists); a stated default for each non-blocking question (the caps of 4 questions and 2 blocking were later removed, see Question policy); tokens and estimated cost are reported (about $0.10 per
issue, roughly 35k tokens in, mostly context). Jev's invention check was validated on
4 issues x 3 variants: faithful improvements scored 0.26 to 0.46, ones with one small
added requirement 0.60 to 0.92, ones with several invented requirements 0.93 to 0.98, so
the 0.5 flag separated them on this small sample. The owner's answer on defaults is in Question policy.

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

## Shift dashboard (Decided: wanted; mechanism is a hypothesis)

A UI to watch a shift as it runs: current stage (reading issues, refining, implementing,
testing), progress such as 4/10 issues, and per-issue status, plus the morning digest.
Also meant as a showcase: it is the easiest way to show what Nightshift does.

The morning digest lives in the dashboard too, so everything is in one place. Later the
digest and its questions can also be pushed to other places (GitHub issue comments, Slack,
email) as extra delivery channels; the dashboard stays the source of truth.

Design consequence, decided now so it is not retrofitted: every stage writes structured
events (shift id, issue, stage, status, timestamp, cost) to the run log. The dashboard only
reads that log. Hypotheses: a small local web app that polls the log; a replay mode that
plays back a recorded shift, so the public repo can demo it without a live run.

## Hypotheses to test (do not treat as fixed)

- Tier names and what each tier means (trivial / small / medium / large).
- Time and cost limits per tier. Start with generous placeholders, tighten from run logs.
- Which model suits which tier and stage. The table below is the baseline; Jev's
  intuition-based picks are compared against it, and run logs show which is better.
  Stage roles (guess): Jev triage and routing; a mid-size model for improving, asking,
  splitting and creating issues; the tier table for implementation; a different model
  from the implementer for review and testing; the cheapest model for the digest text.
  Starting table (from third-party Oct 2026 benchmark roundups, unverified):
  trivial → claude-haiku-5-5, small/medium → claude-sonnet-5-5, large or retries →
  claude-opus-5-5. Codex (OpenAI GPT-6 line) is the first alternative to compare.
- Cross-model review (a different model reviews the diff). Add Codex as an executor/reviewer
  when the basics work.
- Jev thresholds for "high confidence". Tune using the eval, not guesses.
- Splitting `risky` into per-category questions (schema, auth, deps, deploy).
- Proposed-issue and split rules: caps, depth limits, auto-approval later.
- Model for the refine/ask stage (reads code and docs, drafts questions). Starting guess:
  a mid-size model by default, a stronger one when the issue is large or risky or the repo
  is big, a cheaper one for tiny issues. Compare models on invention rate and question
  quality, not cost alone.
- Bump the implementing model up one level when risk is high (the SQLite issue got Sonnet
  because Jev sized it medium, though its risk was high).
- A "needs a business decision" Jev question. Not built yet: the accounts issue (x9) went
  straight to a model pick although it hinges on decisions only the owner can make.
- Manual running-app testing by a browser agent for some tiers.
- Python for the orchestrator (not final).
- Hosting: not the laptop overnight. Build and try it locally first; the orchestrator is a
  CLI that runs from any machine with git, `gh` and the agent CLI. First scheduled home:
  a GitHub Actions cron in the target repo (nothing to keep on; GitHub is already the
  input and output; state kept in labels, comments and a data branch). Alternatives if
  Actions limits bite: a Railway cron service or the Pi. Unattended runs need model
  access. Locally an Anthropic API key in `.env` is simplest. In GitHub Actions, workload
  identity federation (short-lived OIDC tokens, no stored key) is the alternative to a repo
  secret; it needs a Console admin to set up and is only worth it once we run there.
  Per-token billing either way; check current terms for subscription use.

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
- Model-pick experiment (2026-10-26, `nightshift models`): Jev picks the executor from
  model descriptions plus the issue and an optional `budget_usd`. On the 30 recipe cases it
  chose Haiku for 24 to 28, never Opus, and a bigger budget did not make it choose stronger
  models. For the two large and high-risk cases (SQLite, accounts) it chose Sonnet with
  0.8+ confidence, against its own description of Opus. There is no ground truth for
  "best model", so this only shows that the intuition-only pick looks biased cheap.
  Plan: run it in shadow mode (log its pick, use the table) until run logs show whether
  its picks work, and try a table keyed on Jev's size and risk labels (e.g. high risk
  bumps up one model).

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
5. Merge gates, auto-merge to `dev`, revert, overnight loop, digest, and the shift event
   log with a dashboard on top of it.
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
