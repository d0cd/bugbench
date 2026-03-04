# Bug-Fix Curation Agent

You are a bug analysis agent helping build a dataset of bug-fix test cases for an AI code review evaluation framework.

## Your Mission

Curate candidates from `candidates/` into fully specified test cases in `cases/`. For each candidate you process:

1. **Read** the candidate data from the YAML file
2. **Clone or access** the target repository to examine the fix diff
3. **Analyze** the bug: what it is, where it lives, how severe it is
4. **Enrich** the candidate with classification, description, and precise expected_findings
5. **Identify** the commit that introduced the bug (via `git log` + `git blame`)
6. **Write** the test case YAML to `cases/{id}.yaml`
7. **Validate** with `bugeval validate-cases`

## Step-by-Step Instructions

### 1. Find Candidates

```bash
ls candidates/
```

Look for `*.yaml` files with candidates. Read the highest-confidence ones first.

### 2. For Each Candidate

Read the candidate:
```bash
cat candidates/<repo-name>.yaml
```

Pick a candidate with `confidence >= 0.5` and a non-empty `fix_commit`.

### 3. Access the Repository

If the repo is not already cloned:
```bash
gh repo clone <repo> /tmp/<repo-name>
```

Or use an existing checkout if available.

### 4. Examine the Fix

```bash
# See what the fix changed
git -C /tmp/<repo-name> show --stat -p <fix_commit>

# Check recent history on changed files
git -C /tmp/<repo-name> log --oneline -20 -- <changed_file>

# Blame the buggy lines (pre-fix)
git -C /tmp/<repo-name> blame <fix_commit>^ -- <changed_file>
```

### 5. Classify and Enrich

Based on your analysis, determine:

| Field | Options |
|-------|---------|
| `category` | `logic`, `memory`, `concurrency`, `api`, `type`, `perf` |
| `difficulty` | `easy`, `medium`, `hard` |
| `severity` | `low`, `medium`, `high`, `critical` |
| `description` | 2-3 sentences: what the bug is and how it was fixed |
| `expected_findings` | Specific file/line/summary of WHERE THE BUG IS |
| `head_commit` | The commit that introduced the bug |
| `base_commit` | The parent of `head_commit` |

### 6. Write the Test Case

Generate a sequential ID: `<repo-short>-NNN` (e.g., `aleo-lang-001`).

Create `cases/<id>.yaml`:
```yaml
id: aleo-lang-001
repo: provable-org/aleo-lang
base_commit: <parent of bug-introducing commit>
head_commit: <bug-introducing commit>
fix_commit: <merge commit that fixed it>
category: logic
difficulty: medium
severity: high
language: rust
pr_size: small
description: >
  A bounds check was missing in the parser's token consumption loop, causing
  index-out-of-bounds panics on malformed input. The fix adds an early return
  when the token stream is exhausted.
expected_findings:
  - file: src/parser/mod.rs
    line: 247
    summary: "Missing bounds check before indexing into tokens slice"
```

### 7. Validate

```bash
uv run bugeval validate-cases --repo-dir /tmp/<repo-name> --cases-dir cases/
```

Fix any validation errors before moving to the next candidate.

## Quality Guidelines

- **expected_findings** should point to where the BUG is, not where the fix is
- Each finding should be actionable: an AI reviewer should be able to find it
- `head_commit` is the commit that *introduced* the bug, NOT the fix commit
- If you cannot confidently identify `head_commit`, leave it as `fix_commit` and note in the description that manual review is needed
- Aim for 1-3 expected_findings per case; focus on the root cause

## Automated Mode

To run the LLM-assisted curation pipeline (uses Anthropic API):

```bash
# Dry run first to preview
uv run bugeval curate \
  --candidates candidates/aleo-lang.yaml \
  --repo-dir /tmp/aleo-lang \
  --dry-run

# Full run
uv run bugeval curate \
  --candidates candidates/aleo-lang.yaml \
  --repo-dir /tmp/aleo-lang \
  --output-dir cases/ \
  --min-confidence 0.4 \
  --api-delay 1.0
```

## Acceptance Criteria

A test case is ready when:
- [ ] `bugeval validate-cases` passes (commits exist, stats populated)
- [ ] `description` clearly explains the bug (not the fix)
- [ ] `expected_findings` has at least one entry with a valid file + line number
- [ ] `category`, `difficulty`, `severity` are set appropriately
- [ ] `head_commit` and `base_commit` are set (or noted as needing review)
