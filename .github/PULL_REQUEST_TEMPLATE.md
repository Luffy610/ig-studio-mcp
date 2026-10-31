## What does this change?

<!-- One or two sentences, and link the issue it fixes if there is one. -->

## Checklist

- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run pytest` passes, with tests added or updated for the change
- [ ] New write tools take `confirm: bool = False` and make no HTTP calls when it is false
- [ ] Tool docstrings say what the tool does, when to use it, what it returns and its limits
- [ ] README / CHANGELOG updated if behaviour changed
- [ ] No tokens, app secrets, account IDs or `.env` contents in code, tests or this PR
