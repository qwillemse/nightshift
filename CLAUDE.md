# Nightshift

Pipeline that triages GitHub issues, works through them with coding agents, and reports
back. Read `PLAN.md` first: it separates **Decided** items from **Hypotheses**. Don't
promote a hypothesis to a fact without the owner's say-so.

## Commands

```bash
uv sync                      # install (Python 3.13)
uv run pytest                # tests (never calls the real Jev API)
uv run pytest -m live        # live Jev test, needs TYPESAFE_API_KEY in .env
uv run ruff check . && uv run ruff format .
uv run nightshift eval evals/spike_issues.json            # score triage against labels
uv run nightshift triage --repo owner/name --issue 1 [--apply]
uv run nightshift models evals/recipe_issues.json [--budget 1]   # Jev's model picks vs table
uv run nightshift refine --cases evals/recipe_issues.json --case 'recipe#3' --repo-path ../recipe  # dry run, prints only
```

## Layout

- `src/nightshift/decider.py`: `Decider` interface, `JevDecider`, `FakeDecider`
- `src/nightshift/triage.py`: questions, thresholds, issue -> suggested labels
- `src/nightshift/github.py`: `gh` wrapper; `apply_suggestion` never overwrites human labels
- `src/nightshift/store.py`: SQLite log of suggestions, runs and human corrections
- `src/nightshift/evals.py`: accuracy and calibration against labeled cases
- `src/nightshift/llm.py`: text LLM backends (`AnthropicLLM`, `ClaudeCLI`, `FakeLLM`)
- `src/nightshift/context.py`: gathers repo docs, tree and relevant files for a prompt
- `src/nightshift/refine.py`: refine-and-ask step with quote-grounding and Jev invention check
- `evals/`: labeled cases. `spike/`: the original throwaway Jev experiment.

## Rules

- Only an issue's title and body go to the model, never its labels.
- Tests must not call the network; use `FakeDecider` and fake `subprocess.run`.
- `.env` holds secrets and is gitignored. Never commit keys; never paste them in `.env.example`.
