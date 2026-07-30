# Synthetic hard-benchmark router

- **AI-ACCESS:** allowed
- **SCOPE:** this synthetic workspace

## Startup

Read `user-memory\profile.md`, `user-memory\alex\user-settings.yml`,
`agent-memory\INDEX.md`, and the active repository router. Greet Alex briefly in
the effective `chat_language`.

## Rules

- Settings precedence: standards, user override, repository policy.
- Stable user facts: append one concise bullet to `user-memory\profile.md`.
- Reusable agent learnings: append one concise rule to `agent-memory\learnings.md`.
- Inline comments explain non-obvious why, never visible what.
- Keep documentation short; no explanatory prose.
- `AI-ACCESS: denied` stops all changes in that scope.
- Validate code with `python -m unittest discover -s tests -v`.
- New test run: read `docs\benchmark-requirements.md`, decide defined values
  autonomously, and route unresolved owner decisions to `agent-memory\inbox.md`.

## Chat and phase close

For a completed benchmark milestone:

1. Run `python tools\sync_chat_logs.py`.
2. Run `python tools\validate_chat_logs.py`.
3. Update `user-memory\session-log.md` briefly.

Private originals stay ignored. Commits reference only:
`standards/why/conversations/002__ai-summary-public__opencode-1.18.9__siemens-hard-agent__hard-benchmark.md`

## Git

- Do not commit trivial intermediate edits.
- Commit a cohesive tested milestone or a new benchmark run.
- Use:
  `<type>(<scope>): <what> -- <why>`
  `why-ref: standards/why/conversations/<summary>.md`
  `agent: <tool> | model: <model> | role: <role>`
