# kinozal_scraper
The parser analyzes the top kinozal.tv according to the schedule and sends all new films to telegram

## Quality tooling

Install production and development dependencies:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
```

Run the local quality gates:

```bash
python -m ruff format --check .
python -m ruff check .
python -m pytest
```

Run the advisory dependency audit:

```bash
python -m pip_audit -r requirements.txt
```

Regenerate pinned dependency files after editing `requirements.in` or
`requirements-dev.in`:

```bash
python -m piptools compile requirements.in --output-file requirements.txt --strip-extras --upgrade
python -m piptools compile requirements-dev.in --output-file requirements-dev.txt --strip-extras --upgrade
```
