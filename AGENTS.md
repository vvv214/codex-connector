# AGENTS.md

## Review priorities

- Prioritize correctness, safety, privacy, and reproducibility over style-only
  feedback.
- Flag secrets, tokens, credentials, private dataset paths, absolute local
  paths, and machine-specific environment assumptions.
- Flag behavior changes that are not matched by tests, scripts, configuration,
  or documentation updates.
- For research, benchmark, and artifact changes, check for silent metric
  changes, invalid comparisons, benchmark leakage, and missing reproducibility
  details.
- Prefer a few high-signal findings over many low-value comments.
