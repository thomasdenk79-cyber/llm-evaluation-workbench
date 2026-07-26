# Contributing

## Scope

This repository focuses on local LLM evaluation workflows for Oracle to PostgreSQL migration tasks.

## Contribution Rules

1. Keep benchmark changes reproducible:
   - document seeds, model versions, and runtime parameters.
2. Do not commit secrets:
   - API keys, tokens, internal credentials, or private host details are forbidden.
3. Keep output schema stable:
   - if CSV/JSON fields change, update docs and changelog in the same PR.
4. Keep changes focused:
   - avoid unrelated refactors in benchmark or metric scripts.

## Pull Request Checklist

- [ ] README and runbook updated when behavior changed
- [ ] changelog entry added
- [ ] scripts compile/parse successfully
- [ ] benchmark command example still works
