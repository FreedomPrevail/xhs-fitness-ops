# Contributing

## Before opening a change

1. Keep platform reads operator-triggered.
2. Do not enable interaction, unattended publishing, authentication bypass or risk-control evasion.
3. Preserve provenance and keep missing metrics as unknown.
4. Do not merge `golden_score`, `transfer_score` and `revalidation_score` into one stored field.
5. Keep image-model output free of final Chinese typography.
6. Add or update tests for changed behavior.
7. Remove account data, personal writing, generated drafts and credentials from examples.

## Development setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
```

Windows uses `.venv\Scripts\python.exe` instead.

## Pull requests

Keep pull requests focused. Describe the behavior change, safety impact, tests performed and any migration needed for local SQLite/JSON state. Never attach a real runtime database or browser profile.

