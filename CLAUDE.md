# CLAUDE.md - Zelos OPAL-RT Extension

## Overview

Zelos extension for OPAL-RT real-time simulation systems. Currently scaffolding
with synthetic power-system signal generation; real OPAL-RT packages to be
integrated later.

## Quick Reference

```bash
just install    # Install deps + pre-commit hooks
just dev        # Run extension locally
just test       # Run pytest
just check      # Ruff lint
just format     # Ruff format + fix
just package    # Build .tar.gz
```

## Structure

- `main.py` — Entry point (SDK init, signal handlers, run loop)
- `zelos_opal/extension.py` — `OpalMonitor` class (signals, actions, trace schema)
- `config.schema.json` — Config UI schema for Zelos App
- `extension.toml` — Zelos extension manifest
- `tests/test_extension.py` — Unit tests

## Conventions

- Python 3.11+, strict ruff linting
- Formatting: `just format` (ruff format + check --fix)
- Tests use the Zelos checker framework (`check.that(...)`)
- SDK actions must be registered BEFORE `zelos_sdk.init()`
