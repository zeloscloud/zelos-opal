"""Zelos actions for the OPAL-RT extension.

Utility actions (status, list, poll) are registered under the bare ``opal``
namespace.  Per-signal/parameter/variable actions are generated dynamically
at startup from the RT-LAB discovery data and registered under nested
namespaces such as ``opal/read/signal/<path>``.

All RT-LAB interaction is routed through the shared ``_monitor`` which
handles thread-safety internally.
"""

from __future__ import annotations

import logging
from typing import Any

from zelos_sdk import action, actions_registry

logger = logging.getLogger(__name__)

_monitor: Any = None


def _parse_value(raw: str) -> float:
    """Parse a numeric string, accepting hex (``0x…``) notation."""
    raw = raw.strip()
    if raw.lower().startswith("0x"):
        return float(int(raw, 16))
    return float(raw)


def init(monitor: Any) -> None:
    """Set the shared monitor instance (called once after discovery)."""
    global _monitor  # noqa: PLW0603
    _monitor = monitor


# ---------------------------------------------------------------------------
# Utility actions (registered under bare service name)
# ---------------------------------------------------------------------------


@action("Get Status", "Current monitoring status and connection info")
def get_status() -> dict[str, Any]:
    return _monitor.status()


@action("Set Poll Interval", "Change delay between acquisition frames")
@action.number(
    "seconds",
    minimum=1.0,
    maximum=60.0,
    default=1.0,
    title="Interval (seconds)",
    description="Delay between acquisition frames",
    widget="range",
)
def set_poll_interval(seconds: float) -> dict[str, Any]:
    _monitor.config["poll_interval"] = seconds
    return {"message": f"Poll interval set to {seconds}s", "poll_interval": seconds}


@action("List Signals", "List all signals available in the model")
def list_signals() -> dict[str, Any]:
    infos = _monitor.signal_infos
    return {
        "count": len(infos),
        "signals": [
            {
                "name": s.name,
                "path": s.path,
                "label": s.label,
                "type": s.signal_type.name,
            }
            for s in infos
        ],
    }


@action("List Parameters", "List all block parameters in the model")
def list_parameters() -> dict[str, Any]:
    infos = _monitor.param_infos
    return {
        "count": len(infos),
        "parameters": [
            {"name": p.name, "path": p.path, "variable": p.variable, "value": p.value}
            for p in infos
        ],
    }


@action("List Variables", "List MATLAB workspace variables (if available)")
def list_variables() -> dict[str, Any]:
    infos = _monitor.variable_infos
    return {
        "count": len(infos),
        "variables": [{"name": v.name, "value": v.value} for v in infos],
    }


# ---------------------------------------------------------------------------
# Dynamic choices providers (read cached data — always thread-safe)
# ---------------------------------------------------------------------------


def _signal_choices() -> list[str]:
    return [s.path for s in _monitor.signal_infos] if _monitor else []


def _control_signal_choices() -> list[str]:
    return [s.path for s in _monitor.control_signal_infos] if _monitor else []


def _parameter_choices() -> list[str]:
    return [f"{p.path}/{p.name}" for p in _monitor.param_infos] if _monitor else []


def _variable_choices() -> list[str]:
    return [v.name for v in _monitor.variable_infos] if _monitor else []


# ---------------------------------------------------------------------------
# Generic dropdown actions (select from discovered items)
# ---------------------------------------------------------------------------


@action("Read Signal", "Read current value of a signal")
@action.select("name", choices=_signal_choices, title="Signal")
def read_signal(name: str) -> dict[str, Any]:
    return _monitor.read_signals((name,))


@action("Set Signal", "Set a control signal value (accepts hex 0x…)")
@action.select("name", choices=_control_signal_choices, title="Signal")
@action.text("value", title="Value", description="Numeric value (decimal or hex 0x…)")
def set_signal(name: str, value: str) -> dict[str, Any]:
    v = _parse_value(value)
    _monitor.set_signals((name,), (v,))
    return {"message": f"Set {name} = {v}"}


@action("Read Parameter", "Read current value of a parameter")
@action.select("name", choices=_parameter_choices, title="Parameter")
def read_parameter(name: str) -> dict[str, Any]:
    return _monitor.read_parameters((name,))


@action("Set Parameter", "Set a block parameter value (accepts hex 0x…)")
@action.select("name", choices=_parameter_choices, title="Parameter")
@action.text("value", title="Value", description="Numeric value (decimal or hex 0x…)")
def set_parameter(name: str, value: str) -> dict[str, Any]:
    v = _parse_value(value)
    _monitor.set_parameters((name,), (v,))
    return {"message": f"Set {name} = {v}"}


@action("Read Variable", "Read current value of a MATLAB variable")
@action.select("name", choices=_variable_choices, title="Variable")
def read_variable(name: str) -> dict[str, Any]:
    return _monitor.read_variable(name)


@action("Set Variable", "Set a MATLAB workspace variable value (accepts hex 0x…)")
@action.select("name", choices=_variable_choices, title="Variable")
@action.text("value", title="Value", description="Numeric value (decimal or hex 0x…)")
def set_variable(name: str, value: str) -> dict[str, Any]:
    return _monitor.set_variable(name, _parse_value(value))


_UTILITY_ACTIONS = [
    get_status,
    set_poll_interval,
    list_signals,
    list_parameters,
    list_variables,
    read_signal,
    set_signal,
    read_parameter,
    set_parameter,
    read_variable,
    set_variable,
]


# ---------------------------------------------------------------------------
# Action factories — each returns a decorated action function
# ---------------------------------------------------------------------------


def _leaf(path: str) -> str:
    """Return the last segment of a ``/``-delimited path."""
    return path.rsplit("/", 1)[-1]


def _dynamic_action(fn: Any, title: str, desc: str) -> Any:
    """Decorate *fn* as an action while suppressing SDK auto-registration.

    The SDK's ``action()`` decorator auto-registers regular functions in the
    global registry (using ``__name__``).  Setting ``__qualname__`` to contain
    a ``.`` makes it look like an instance method, which the SDK skips.
    We then register explicitly via ``actions_registry.register(fn, name=…)``.
    """
    fn.__qualname__ = f"_dynamic.{fn.__name__}"
    return action(title, desc)(fn)


def _make_read_signal(path: str) -> Any:
    def _fn() -> dict[str, Any]:
        return _monitor.read_signals((path,))

    _fn.__name__ = f"read_sig_{id(_fn)}"
    return _dynamic_action(_fn, f"Read {_leaf(path)}", f"Read signal {path}")


def _make_set_signal(path: str) -> Any:
    @action.text("value", title="Value", description="Numeric value (decimal or hex 0x…)")
    def _fn(value: str) -> dict[str, Any]:
        v = _parse_value(value)
        _monitor.set_signals((path,), (v,))
        return {"message": f"Set {path} = {v}"}

    _fn.__name__ = f"set_sig_{id(_fn)}"
    return _dynamic_action(_fn, f"Set {_leaf(path)}", f"Set control signal {path}")


def _make_read_parameter(api_path: str) -> Any:
    def _fn() -> dict[str, Any]:
        return _monitor.read_parameters((api_path,))

    _fn.__name__ = f"read_param_{id(_fn)}"
    return _dynamic_action(_fn, f"Read {api_path}", f"Read parameter {api_path}")


def _make_set_parameter(api_path: str) -> Any:
    @action.text("value", title="Value", description="Numeric value (decimal or hex 0x…)")
    def _fn(value: str) -> dict[str, Any]:
        v = _parse_value(value)
        _monitor.set_parameters((api_path,), (v,))
        return {"message": f"Set {api_path} = {v}"}

    _fn.__name__ = f"set_param_{id(_fn)}"
    return _dynamic_action(_fn, f"Set {api_path}", f"Set parameter {api_path}")


def _make_read_variable(name: str) -> Any:
    def _fn() -> dict[str, Any]:
        return _monitor.read_variable(name)

    _fn.__name__ = f"read_var_{id(_fn)}"
    return _dynamic_action(_fn, f"Read {name}", f"Read variable {name}")


def _make_set_variable(name: str) -> Any:
    @action.text("value", title="Value", description="Numeric value (decimal or hex 0x…)")
    def _fn(value: str) -> dict[str, Any]:
        return _monitor.set_variable(name, _parse_value(value))

    _fn.__name__ = f"set_var_{id(_fn)}"
    return _dynamic_action(_fn, f"Set {name}", f"Set variable {name}")


# ---------------------------------------------------------------------------
# Registration — generates dynamic actions from discovery data
# ---------------------------------------------------------------------------


def register() -> None:
    """Register utility + dynamically generated actions with the Zelos SDK."""
    for fn in _UTILITY_ACTIONS:
        actions_registry.register(fn)

    if _monitor is None:
        return

    counts: dict[str, int] = {}
    errors = 0

    def _try_register(factory, path: str, namespace: str) -> None:
        nonlocal errors
        try:
            actions_registry.register(factory(path), name=f"{namespace}/{path}")
            counts[namespace] = counts.get(namespace, 0) + 1
        except Exception:
            errors += 1
            logger.warning("Failed to register %s/%s", namespace, path, exc_info=True)

    # Per-path read actions are intentionally NOT generated — users should
    # read via the nominal tracing pipeline.  The generic dropdown-based
    # read_* utility actions remain available for ad-hoc reads.
    for s in _monitor.control_signal_infos:
        _try_register(_make_set_signal, s.path, "set/signal")

    for p in _monitor.param_infos:
        api_path = f"{p.path}/{p.name}"
        _try_register(_make_set_parameter, api_path, "set/parameter")

    for v in _monitor.variable_infos:
        _try_register(_make_set_variable, v.name, "set/variable")

    summary = ", ".join(f"{c} {ns}" for ns, c in counts.items()) or "none"
    logger.info("Registered dynamic actions: %s", summary)
    if errors:
        logger.warning("Skipped %d action(s) due to errors", errors)
