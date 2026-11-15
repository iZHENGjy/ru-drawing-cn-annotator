# Contributing

## Set up

```bash
git clone https://github.com/iZHENGjy/ru-drawing-cn-annotator && cd ru-drawing-cn-annotator
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,scan]"
pre-commit install
drawtrans doctor
```

## Before a pull request

```bash
ruff check src tests scripts
mypy
pytest -q -m "not e2e and not regression"     # unit, seconds
pytest -q -m e2e                              # synthetic end-to-end runs
pytest -q -m regression                       # layout baselines
```

A change that moves translated text must say so: the layout regression (`layout_sha256`) fails, you look at
the output, and the baseline is updated in the same pull request with the reason (see
[docs/architecture.md](docs/architecture.md#regression)). Approved client layouts are protected by switches
(`[scan] output_standard`, `[job] optimize`, `[scan] quality_rules`): new behaviour is opt-in for old projects.

## Rules

- **No client data** in the repository or in commit messages: no names, stamps, drawing numbers, project
  codes. Tests and demos use synthetic data only (`demo/`, `tests/`). Keep real projects outside the
  repository or under `projects/` (ignored).
- Where things go: [docs/architecture.md](docs/architecture.md) (module map, extension points).
- The output rules are [docs/output_standard.md](docs/output_standard.md); a new rule also gets a check in
  `qa/standard.py`.
- LLM work only through the constrained tasks in `src/drawtrans/llm/tasks/` (schema + code validator + cache).
- Record notable changes in [CHANGELOG.md](CHANGELOG.md).
