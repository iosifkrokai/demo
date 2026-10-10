@AGENTS.md

Claude-only notes:

- Prefer `make test` over invoking the tools by hand; it is what CI runs.
- Live tests need the stack up and a model key. A stack without
  `OPENROUTER_API_KEY` is a valid state, not a broken one — planning endpoints
  answer `503 llm_not_configured` on purpose.
