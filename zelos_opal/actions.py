"""Zelos actions for the OPAL-RT extension.

Free-standing ``@action`` functions with dynamic dropdowns.  All RT-LAB
interaction is routed through the shared ``_monitor`` which handles
thread-safety internally — actions stay pure and simple.
"""

from __future__ import annotations

from typing import Any

from zelos_sdk import action, actions_registry

_monitor: Any = None


def init(monitor: Any) -> None:
    """Set the shared monitor instance (called once at startup)."""
    global _monitor  # noqa: PLW0603
    _monitor = monitor


def register() -> None:
    """Register all OPAL actions with the Zelos SDK."""
    for fn in _actions:
        actions_registry.register(fn)


# ---------------------------------------------------------------------------
# Dynamic choices providers (read cached data — always thread-safe)
# ---------------------------------------------------------------------------


def _signal_choices() -> list[str]:
    return [s.path for s in _monitor.signal_infos] if _monitor else []


def _control_signal_choices() -> list[str]:
    return [s.path for s in _monitor.control_signal_infos] if _monitor else []


def _parameter_choices() -> list[str]:
    return [p.path for p in _monitor.param_infos] if _monitor else []


def _variable_choices() -> list[str]:
    return [v.name for v in _monitor.variable_infos] if _monitor else []


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@action("Get Status", "Current monitoring status and connection info")
def get_status() -> dict[str, Any]:
    return _monitor.status()


@action("Set Poll Interval", "Change delay between acquisition frames")
@action.number(
    "seconds",
    minimum=0.001,
    maximum=60.0,
    default=1.0,
    title="Interval (seconds)",
    description="Delay between acquisition frames",
    widget="range",
)
def set_poll_interval(seconds: float) -> dict[str, Any]:
    _monitor.config["poll_interval"] = seconds
    return {"message": f"Poll interval set to {seconds}s", "poll_interval": seconds}


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


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


@action("Read Signal", "Read current value of a signal")
@action.select("name", choices=_signal_choices, title="Signal")
def read_signal(name: str) -> dict[str, Any]:
    return _monitor.read_signals((name,))


@action("Set Signal", "Set a signal value")
@action.select("name", choices=_signal_choices, title="Signal")
@action.number("value", title="Value")
def set_signal(name: str, value: float) -> dict[str, Any]:
    _monitor.set_signals((name,), (value,))
    return {"message": f"Set {name} = {value}"}


# ---------------------------------------------------------------------------
# Control signals
# ---------------------------------------------------------------------------


@action("List Control Signals", "List control signals in the model")
def list_control_signals() -> dict[str, Any]:
    infos = _monitor.control_signal_infos
    return {
        "count": len(infos),
        "control_signals": [{"name": s.name, "path": s.path, "label": s.label} for s in infos],
    }


@action("Read Control Signals", "Read all current control signal values")
def read_control_signals() -> dict[str, Any]:
    return _monitor.read_control_signals()


@action("Set Control Signals", "Set control signal values for a subsystem")
@action.integer("subsystem_id", minimum=0, maximum=16, default=1, title="Subsystem ID")
@action.text(
    "values",
    title="Values",
    description="Comma-separated numeric values",
)
def set_control_signals(subsystem_id: int, values: str) -> dict[str, Any]:
    vals = tuple(float(v.strip()) for v in values.split(",") if v.strip())
    _monitor.set_control_signals(int(subsystem_id), vals)
    return {"message": f"Set {len(vals)} control signal(s)"}


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


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


@action("Read Parameter", "Read current value of a parameter")
@action.select("name", choices=_parameter_choices, title="Parameter")
def read_parameter(name: str) -> dict[str, Any]:
    return _monitor.read_parameters((name,))


@action("Set Parameter", "Set a block parameter value")
@action.select("name", choices=_parameter_choices, title="Parameter")
@action.number("value", title="Value")
def set_parameter(name: str, value: float) -> dict[str, Any]:
    _monitor.set_parameters((name,), (value,))
    return {"message": f"Set {name} = {value}"}


# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


@action("List Variables", "List workspace variables in the model")
def list_variables() -> dict[str, Any]:
    infos = _monitor.variable_infos
    return {
        "count": len(infos),
        "variables": [{"name": v.name, "value": v.value} for v in infos],
    }


@action("Read Variable", "Read current value of a variable")
@action.select("name", choices=_variable_choices, title="Variable")
def read_variable(name: str) -> dict[str, Any]:
    return _monitor.read_variables((name,))


@action("Set Variable", "Set a workspace variable value")
@action.select("name", choices=_variable_choices, title="Variable")
@action.number("value", title="Value")
def set_variable(name: str, value: float) -> dict[str, Any]:
    _monitor.set_variables((name,), (value,))
    return {"message": f"Set {name} = {value}"}


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

_actions = [
    get_status,
    set_poll_interval,
    list_signals,
    read_signal,
    set_signal,
    list_control_signals,
    read_control_signals,
    set_control_signals,
    list_parameters,
    read_parameter,
    set_parameter,
    list_variables,
    read_variable,
    set_variable,
]
