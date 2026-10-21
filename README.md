# Nightshift

Issue-to-merge pipeline that works through a GitHub backlog overnight. A human reviews the
result in the morning. Early stage: triage and evals work; implementation is not built yet.

See [PLAN.md](PLAN.md) for the design, what is decided, and what is still a hypothesis.

## Try it

```bash
uv sync
cp .env.example .env          # add your TypeSafe key (https://console.typesafe.ai)
uv run pytest
uv run nightshift eval evals/spike_issues.json
```
