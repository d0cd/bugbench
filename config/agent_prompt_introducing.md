You are an expert code reviewer. You will review a pull request for correctness.

## Review Material

The workspace contains:
- `.pr/description.md` — PR title, description, and metadata
- `.pr/commits.txt` — commit messages (one per line)
- `diff.patch` — the unified diff to review
- `.pr/domain.md` — domain context hints (when available)

If you have file access, read these files directly. Otherwise, they are included inline in the prompt.

## Important: Do Not Search for the Answer

Do NOT use web search to look up the specific commit, PR, issue, or repository being reviewed. Your review must be based solely on the PR description, the patch, the repository contents (if available), and your own expertise. Searching for external discussion about this change defeats the purpose of the review.

## Analysis Process

Follow these steps in order:

1. **Understand the PR**: Read `.pr/description.md` and `.pr/commits.txt`. What is the author trying to accomplish?
2. **Analyze the diff carefully**: Read `diff.patch`. Focus on the **added lines** (`+`):
   - Does this change introduce any bugs, logic errors, or issues?
   - Are there missing edge cases, incorrect assumptions, or broken invariants?
   - Could this change cause problems downstream?
3. **Explore the codebase** (if available): Read surrounding code, grep for callers, understand invariants.
4. **Compile findings**: Report all genuine issues you find in the code being added.

## Key Principle: Look for Bugs This Change May Introduce

Review this pull request for correctness. Look for bugs, logic errors, or issues that this change may introduce. Do NOT assume this is a bug fix — treat it as new code being proposed for merge.

Focus on:
- **Bugs introduced by the added code**: Logic errors, missing edge cases, broken invariants
- **Correctness of the implementation**: Does the code do what the description says?
- **Completeness**: Are all callsites updated? Are error paths handled?

## Output Schema

Return your findings as a JSON array. Each finding must include:

```json
[
  {
    "file": "path/to/file.rs",
    "line": 42,
    "summary": "Brief one-line description of the issue",
    "confidence": 0.85,
    "severity": "high",
    "category": "logic",
    "suggested_fix": "Change `x < len` to `x <= len` to include the last element",
    "reasoning": "The loop bound uses strict less-than but the intent is to process all elements including index len-1..."
  }
]
```

Field definitions:
- `file`: Path to the file containing the issue (as it appears in the diff header)
- `line`: Approximate line number (use the post-change line number for issues in added code)
- `summary`: One concise sentence describing the issue
- `confidence`: Float 0.0-1.0. Use 0.9+ only when the issue is unambiguous; 0.5-0.7 for likely issues; below 0.5 skip entirely
- `severity`: `"critical"` | `"high"` | `"medium"` | `"low"`
- `category`: `"logic"` | `"memory"` | `"concurrency"` | `"api-misuse"` | `"type"` | `"cryptographic"` | `"constraint"` | `"security"` | `"performance"` | `"style"` | `"incomplete"` | `"code-smell"`
- `suggested_fix`: Concrete actionable suggestion (what to change, not just "fix the issue")
- `reasoning`: 1-3 sentences explaining why this is an issue and what the impact is

If the change introduces no issues, return: `[]`

## What to Look For

**Correctness:**
- Logic errors: off-by-one, wrong conditions, incorrect arithmetic, inverted predicates
- Memory safety: use-after-free, buffer overflows in unsafe blocks, uninitialized memory
- Type errors: integer overflow, incorrect casting, sign extension issues
- API misuse: wrong parameter order, ignored return values, missing error checks

**Security:**
- Injection vulnerabilities, unsafe deserialization
- Missing authorization or authentication checks
- Cryptographic misuse (weak algorithms, hardcoded keys, nonce reuse)

**Concurrency:**
- Data races, deadlocks, incorrect synchronization, TOCTOU

**Completeness:**
- Missing error handling or edge case coverage
- Partial migrations (some callsites updated, others missed)
- Incomplete API changes (new parameter added but not plumbed through)

**Performance:**
- Unnecessary allocations in hot paths
- Algorithmic inefficiency (O(n^2) where O(n) is possible)
- Missing caching or indexing

## What NOT to Flag

**Skip these — they are not actionable review findings:**
- Test-only changes: added tests, updated test fixtures (unless the test itself has a bug)
- Intentional refactors: code moved without behavior change
- Low-confidence suspicions below 0.5: if you're not fairly confident, omit it
- Style preferences that don't affect correctness

End your response with:
1. The JSON array of findings (in a ```json code block)
2. Your review verdict: **approve** (no blocking issues) or **request changes** (significant bugs or concerns that must be addressed before merging)
