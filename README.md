# bug-tools-eval

Build-vs-buy evaluation: commercial AI code review tools vs. an in-house Claude Code agent,
tested against a curated dataset of real bug-fix PRs from Provable and open-source repos.

## Prerequisites

- Python 3.11+, [uv](https://docs.astral.sh/uv/)
- `gh` CLI (authenticated)
- Docker (required only for `--use-docker` Claude CLI runs)

## Quick start

```bash
git clone <repo-url> && cd bug-tools-eval
uv sync
cp .env.example .env          # fill in ANTHROPIC_API_KEY at minimum
uv run bugeval validate-env --cases-dir cases/final
uv run bugeval run-agent-eval --tools claude-agent-sdk-sonnet --limit 1 --dry-run
```

## Dataset

1,110 curated bug-fix cases across 9 repos, 1,110 patches extracted.

| Repo | Cases | Language |
|------|-------|----------|
| ProvableHQ/leo | 304 | Rust |
| ProvableHQ/snarkVM | 232 | Rust |
| ProvableHQ/snarkOS | 223 | Rust |
| ProvableHQ/sdk | 56 | Rust/JS |
| getsentry/sentry | 63 | Python |
| calcom/cal.com | 88 | TypeScript |
| grafana/grafana | 68 | Go/TypeScript |
| keycloak/keycloak | 43 | Java |
| discourse/discourse | 33 | Ruby |

Cases live in `cases/final/`. Patches live in `patches/`.

## Tools evaluated

| Tool | Type | Model / API |
|------|------|-------------|
| claude-agent-sdk-sonnet | agent | claude-sonnet-4-6 |
| claude-agent-sdk-opus | agent | claude-opus-4-6 |
| claude-cli-sonnet | agent (CLI) | claude-sonnet-4-6 |
| gemini-cli-flash | agent (CLI) | gemini-2.5-flash |
| codex-cli-o4 | agent (CLI) | o4-mini |
| google-api-flash | agent (API) | gemini-2.5-flash |
| openai-api-o4 | agent (API) | o4-mini |
| greptile | api | Greptile v2 |
| coderabbit | pr | GitHub PR review |
| bugbot | pr | GitHub PR review |

See `config/config.yaml` for the full list including all model tiers.

## Running the full experiment

See [`docs/runbook.md`](docs/runbook.md) for the step-by-step guide.

## Results

Each run produces a directory under `results/run-YYYY-MM-DD/` containing:

- `run_metadata.json` — git SHA, tools, context level, dataset commit, case count
- `checkpoint.yaml` — resumable progress state
- `raw/<case-id>-<tool>/` — raw tool outputs (findings, conversation, metadata)
- `<case-id>-<tool>.yaml` — normalized findings (after `normalize`)
- `judge/<case-id>-<tool>.yaml` — LLM judge scores (after `judge`)
- `analysis/` — report, charts, CSV (after `analyze`)

See [`docs/results-schema.md`](docs/results-schema.md) for field-by-field documentation.

## Architecture

```
run-*-eval → normalize → judge → analyze
```

Or run everything at once:

```bash
uv run bugeval pipeline --run-dir results/run-2025-01-01
```

## Development

```bash
uv run pytest
uv run ruff check src/ tests/
uv run pyright src/
```
