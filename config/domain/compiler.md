# Leo Compiler Domain Rules

Use these rules to validate changes. Violations are bugs.

## Operator Type Rules
- `mod` (remainder): ONLY unsigned integers (u8, u16, u32, u64, u128). Signed integers are INVALID.
- `pow`: unsigned integers and field only.
- Bitwise ops (`&`, `|`, `^`, `<<`, `>>`): integers only — not field, group, or scalar.
- Comparison (`<`, `>`, `<=`, `>=`): integers, field, scalar — NOT group or address.
- Equality (`==`, `!=`): all types including address, group, struct.

## Type System
- Struct/record names must NOT contain "aleo" (reserved for program identifiers).
- Function names must NOT start with `__` (reserved for compiler internals).
- `const` generics must be const-evaluated to literals BEFORE monomorphization. `Foo::[2*8]` and `Foo::[16]` must resolve to the same monomorphized type.
- External struct types require program-qualified names: `parent.aleo/MyStruct`.
- Futures can appear in tuples — type equality comparisons must use relaxed comparison (`eq_flat_relaxed`), not strict comparison.

## Parser Rules
- Negative literals: `-5field` is a single literal token, NOT unary negation of `5field`.
- Double negation: `-(-x)` must produce `UnaryMinus(UnaryMinus(x))`, not the string `--x`.
- Empty arrays `[]` and `[T; 0]`: support depends on language version.
- Struct members MUST be separated by commas. No semicolons, no bare newlines.
- In Logos lexer: `#[token("...")]` matches exact strings only. Use `#[regex("...")]` for patterns. A `#[token(r"group::[a-zA-Z]...")]` will NEVER match — the regex is treated as a literal string.

## Compiler Passes (common bug sites)
- **SSA pass**: must NOT replace paths to external globals (`child.aleo/foo`) with local variable names when they share a name.
- **Const propagation**: must preserve local constants across pass boundaries. `reset_but_consts()` should not clear locals needed for loop unrolling.
- **Function inlining**: output must have no variable name shadowing. Inlined variables must be renamed.
- **Flattening**: conditional `finalize` blocks need special handling — flattening can change execution semantics.
- **Dead code elimination**: must not remove code with side effects (mapping operations, state writes).
- **Monomorphization**: const arguments must be fully evaluated before generating monomorphized type names.

## CLI Rules
- `leo clean`: must check directory exists before calling `remove_dir_all`. Missing directory = error on the user.
- `leo deploy`: skipped (already-deployed) programs must STILL be added to the local VM via `add_program`. Skipping deployment != skipping VM registration.
- `leo execute`: a positional `Vec<String>` argument greedily consumes all remaining args. A following positional `Option<String>` will never be populated — use `#[clap(long)]` instead.
- Default network should be `testnet`, not `mainnet`, for development tooling.
- `--seed` flag for signature nonces is cryptographically insecure — deterministic nonces can leak private keys.

## Display/Formatting
- AST `Display` implementations must respect operator precedence when inserting parentheses.
- Struct `Display` must include field separator commas.
- Function `Display` must correctly render output types (single vs tuple).
- Import paths: `.leo` extension for source files, `.aleo` for compiled programs.

## Scope and Nesting (Critical)
- Rust's `cargo fmt` can re-indent code, changing which scope a statement belongs to. A statement moving from outside an `if` block to inside it changes behavior silently. Watch for `}}` becoming `}\n    }` on separate lines — this can move the preceding statement into an inner scope.
