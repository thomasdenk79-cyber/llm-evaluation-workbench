# Chat naming clarification

Apply these rules literally:

- `TOOL` includes normalized tool name and version: `OpenCode 1.18.9` becomes
  `opencode-1.18.9`.
- Replace `/` and whitespace inside identifiers with `-`:
  `siemens/deepseek-v4-flash` becomes `siemens-deepseek-v4-flash`.
- Use lowercase native extensions. OpenCode uses `.json`.
- The first original snapshot is `RR=01`; the second is `RR=02`.
- A public summary uses the literal class token `public`.
- A commit `why-ref:` uses the same summary filename with forward slashes.

Worked example for chat 042, OpenCode 1.18.9, model
`siemens/deepseek-v4-flash`, topic `epic-music`:

```text
user-memory\why\conversations\042-01__original-restricted__opencode-1.18.9__siemens-deepseek-v4-flash__epic-music.json
standards\why\conversations\042__ai-summary-public__opencode-1.18.9__siemens-deepseek-v4-flash__epic-music.md
user-memory\why\conversations\042-02__original-restricted__opencode-1.18.9__siemens-deepseek-v4-flash__epic-music.json
why-ref: standards/why/conversations/042__ai-summary-public__opencode-1.18.9__siemens-deepseek-v4-flash__epic-music.md
```
