# AGENTS.md

Instructions for AI coding agents working in this repository.

## Installing or using it for someone

Read [docs/LLM-GUIDE.md](docs/LLM-GUIDE.md). It has non-interactive install commands, health
checks, API examples and troubleshooting.

## Changing the code

Two independent codebases share only the HTTP contract. `app/` is the Python FastAPI backend,
with hand-written static files in `app/static/` and no build step. `tray/` and `tray.Tests/` are
the C# / .NET 9 desktop client. [docs/GUIDE.md](docs/GUIDE.md#layout) maps the files.

```bash
python -m pytest tests/ -v        # backend (inside .venv; on Windows .\.venv\Scripts\python.exe -m pytest tests\ -v)
dotnet test tray.Tests            # desktop client
```

These invariants are the point of the project. Don't break them:

- **Nothing is written to disk.** Token mappings live in memory only (`app/redaction.py`).
- **Never log or return prompt content.** Log engine names and error classes only.
- **No network egress at runtime.** `tests/test_no_egress.py` fails on any outbound
  connection. That is deliberate.
- **All test and sample data is synthetic**: `jane.doe@example.com`, `4111 1111 1111 1111`,
  private-range IPs.
- **Auth stays optional.** With no `REDACTION_API_KEY`, every route stays open.
- **The desktop client always sends `store_session: false`.** It keeps the mapping locally.

When launcher flags, routes, request fields or error messages change, update
[docs/LLM-GUIDE.md](docs/LLM-GUIDE.md) and [llms.txt](llms.txt) too. They quote them verbatim.
