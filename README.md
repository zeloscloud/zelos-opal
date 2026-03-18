# Zelos OPAL-RT Extension

Zelos extension for OPAL-RT real-time simulation systems.

Currently provides basic scaffolding with synthetic power-system signal
generation. Custom OPAL-RT packages will be integrated in a future iteration.

## Quick Start

```bash
just install       # Install dependencies
just dev           # Run locally (generates synthetic signals)
just test          # Run tests
just check         # Lint
just format        # Auto-format
just package       # Build .tar.gz for install
```

## Configuration

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `demo` | boolean | `false` | Run with built-in data generation |
| `endpoint` | string | `localhost:5100` | OPAL-RT target endpoint |
| `poll_interval` | number | `1.0` | Data acquisition interval (seconds) |
| `log_level` | string | `INFO` | Logging level |

## Signals

The `analog_outputs` event provides:

- `va_rms`, `vb_rms`, `vc_rms` — 3-phase voltage RMS (V)
- `ia_rms`, `ib_rms`, `ic_rms` — 3-phase current RMS (A)
- `frequency` — Grid frequency (Hz)
- `active_power` — Active power (W)
- `reactive_power` — Reactive power (VAR)
- `status` — 0=OK, 1=WARNING, 2=FAULT

## License

MIT License - see [LICENSE](LICENSE) for details.
