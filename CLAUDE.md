# bug-tools-eval

Build-vs-buy evaluation framework: commercial AI code review tools vs. an in-house Claude Code agent, tested against Provable repos.

## Stack

- Python 3.11+, uv
- `gh` CLI, Docker, GitHub API
- Click (all CLI commands), Pydantic (schemas), PyYAML, Anthropic SDK, Matplotlib

## Directory Layout

```
cases/          # Test case YAML definitions (immutable during runs)
patches/        # git format-patch outputs
src/bugeval/    # Python package — all scripts live here
tests/          # pytest tests
results/        # Run outputs (gitignored)
config/         # config.yaml, prompt templates
docs/           # Experiment design, onboarding
```

## Conventions

### Test Cases
- Defined in YAML under `cases/`
- Schema validated by Pydantic at run time
- **Immutable** once a run starts — never edit mid-run

### Results
- Stored as `results/run-{YYYY-MM-DD}/`
- `results/` is gitignored — never commit outputs
- Dataset versions tagged `dataset-v1`, `dataset-v2`, etc.

### Scoring (0–3 scale)
| Score | Label | Meaning |
|-------|-------|---------|
| 0 | missed | Bug not identified |
| 1 | wrong-area | Flagged nearby but wrong location |
| 2 | correct-id | Correct file + line, no fix |
| 3 | correct-id-and-fix | Correct ID + actionable fix suggestion |

### Code Style
- All CLI commands use `click`
- Type hints required on all functions
- Docstrings on public functions only (not private/internal helpers)
- Max line length: 100

### Context Levels
Runs are parameterized by how much context the tool receives:
- `diff-only` — only the patch
- `diff+repo` — patch + full repo checkout
- `diff+repo+domain` — patch + repo + domain-specific prompt

## Rules

- **No secrets in code** — use `.env` + `python-dotenv`; see `.env.example`
- **`results/` is gitignored** — never commit run outputs
- **Tests required** for all non-trivial logic
- **No new dependencies** without approval
- **Never commit** without being explicitly asked

## Commands

```bash
# Setup
uv sync

# Run all checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/
uv run pytest

# CLI help
uv run bugeval --help
```
