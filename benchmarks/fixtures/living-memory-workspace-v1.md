# Synthetic C:\GIT fixture

This fixture is artificial benchmark data. Treat the listed files as the complete
virtual workspace. Do not claim that you accessed a real filesystem.

## C:\GIT\AGENTS.md

- On input `C:\GIT`, read this router, `user-memory\profile.md`,
  `agent-memory\INDEX.md`, and the active repository router.
- Greet the user Alex personally and briefly.
- Settings precedence: standards default, then user override, then repository
  owner policy. A deeper `AI-ACCESS: denied` stops work in that scope; an agent
  cannot grant itself access.
- Memory routing:
  - project build commands and state -> repository `AGENTS.md`
  - stable personal facts -> `user-memory\profile.md`
  - reusable agent learnings -> `agent-memory` through `INDEX.md`
  - repo-less open work -> `agent-memory\inbox.md`
- Work principles: SSOT/canonical sources, code as documentation, comments explain
  non-obvious why rather than visible what, append-only/GoBD history, documentation
  answers relevant W-questions, best-practice defaults with owner control, concise
  communication, hierarchical inheritance, and separation of user/agent/project memory.
- Before session end persist technical state and next action, route durable memory,
  record validation and known errors, and commit/push only according to policy.

## C:\GIT\standards\settings.yml

default_ai_chat_logging: summary
transcript_secret_handling: redact

## C:\GIT\user-memory\alex\settings.yml

default_ai_chat_logging: transcript
transcript_secret_handling: redact

## C:\GIT\user-memory\profile.md

- User: Alex Example (synthetic)
- Family: one sibling; other family details are unknown.
- Hobbies: hiking and synthesizers.
- Preferences: concise factual answers and explicit uncertainty.
- Working style: pragmatic, best-practice oriented, optimizes the broad system.

## C:\GIT\agent-memory\INDEX.md

Read only entries whose READ-WHEN matches the task:
- `principles.md`: always; contains the confirmed SSOT and restraint principles.
- `learnings.md`: READ-WHEN evaluating agent behavior; confirmed learning: prefer
  one clear rule over duplicated prose.
- `hypotheses.md`: READ-WHEN diagnosing weak models; active hypothesis [UNSICHER]:
  some failures are retrieval failures rather than comprehension failures.
- `observations.md`: READ-WHEN benchmarking; observation: small models often list
  directories instead of following routers.
- `conflicts.md`: READ-WHEN changing policy; open conflict: detail helps weak models
  but can reduce clarity for commonly used models.
- `inbox.md`: repo-less TODO: add weighted cross-model analysis.

## C:\GIT\demo\AGENTS.md

- AI-ACCESS: allowed
- Build: `python -m unittest`
- Open TODO: add a provider privacy gate.

## C:\GIT\blocked\AGENTS.md

- AI-ACCESS: denied

## Chat evidence rules

- Original: `user-memory\why\conversations\NNN-RR__original-restricted__TOOL__MODELS__TOPIC.NATIVE`
- Summary: `standards\why\conversations\NNN__ai-summary-CLASS__TOOL__MODEL__TOPIC.md`
- OpenCode originals use JSON. Snapshot two uses `RR=02`.
- A commit `why-ref:` may reference only a neutral central summary with forward
  slashes, never a private original.
- Do not add timestamps, hashes, native session IDs, or metadata sidecars.
- If restricted content cannot be neutralized, keep it private, publish nothing
  in `standards\why`, and do not add a commit reference.
- An authorized user's `preserve` override permits secrets only in the protected
  private original. Secrets remain forbidden in commits and central summaries;
  private access protection is the user's responsibility.
- Commit format:
  `<type>(<scope>): <what> -- <why>`
  `why-ref: standards/why/conversations/<summary>.md`
  `agent: <tool> | model: <model> | role: <role>`

## Canonical open work

- demo project: add a provider privacy gate.
- repo-less: add weighted cross-model analysis.
