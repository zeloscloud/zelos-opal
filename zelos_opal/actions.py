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
    return [f"{p.path}/{p.name}" for p in _monitor.param_infos] if _monitor else []


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


@action("Set Signal", "Set a control signal value (dynamic signals are read-only)")
@action.select("name", choices=_control_signal_choices, title="Signal")
@action.number("value", title="Value")
def set_signal(name: str, value: float) -> dict[str, Any]:
    _monitor.set_signals((name,), (value,))
    return {"message": f"Set {name} = {value}"}


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
# Variables (MATLAB workspace — best-effort, not all models define these)
# ---------------------------------------------------------------------------


def _variable_choices() -> list[str]:
    return [v.name for v in _monitor.variable_infos] if _monitor else []


@action("List Variables", "List MATLAB workspace variables (if available)")
def list_variables() -> dict[str, Any]:
    infos = _monitor.variable_infos
    return {
        "count": len(infos),
        "variables": [{"name": v.name, "value": v.value} for v in infos],
    }


@action("Read Variable", "Read current value of a MATLAB variable")
@action.select("name", choices=_variable_choices, title="Variable")
def read_variable(name: str) -> dict[str, Any]:
    return _monitor.read_variable(name)


@action("Set Variable", "Set a MATLAB workspace variable value")
@action.select("name", choices=_variable_choices, title="Variable")
@action.number("value", title="Value")
def set_variable(name: str, value: float) -> dict[str, Any]:
    _monitor.set_variable(name, value)
    return {"message": f"Set {name} = {value}"}


# ---------------------------------------------------------------------------
# Example: dedicated per-signal actions (no dropdown, hardcoded paths)
#
# These demonstrate how to build a focused panel for a single channel.
# The Zelos App caches form inputs in the layout, so each panel instance
# remembers its configuration.  For models with many channels, either
# duplicate these with different paths or use the generic actions above
# whose dropdown selections are also cached per-panel.
# ---------------------------------------------------------------------------

_CH00_DATA_1 = "Automation_Demo/sm_master/CAN_Control/CH00_Control/Data_1_TRIM_Block"


@action("CH00 Data_1 Enable", "Toggle error injection on CH00 Data_1")
@action.boolean("enabled", widget="toggle", default=False, title="Error Active")
def ch00_data1_enable(enabled: bool) -> dict[str, Any]:
    value = 1.0 if enabled else 0.0
    path = f"{_CH00_DATA_1}/Data_1_ENABLE/Value"
    _monitor.set_parameters((path,), (value,))
    return {"message": f"Data_1 error {'ON' if enabled else 'OFF'}"}


@action("CH00 Data_1 Set Gain", "Set gain for CH00 Data_1 error injection")
@action.number("value", title="Gain", default=1.0)
def ch00_data1_gain(value: float) -> dict[str, Any]:
    path = f"{_CH00_DATA_1}/Data_1_GAIN/Gain"
    _monitor.set_parameters((path,), (value,))
    return {"message": f"Data_1 Gain = {value}"}


@action("CH00 Data_1 Set Bias", "Set bias for CH00 Data_1 error injection")
@action.number("value", title="Bias", default=0.0)
def ch00_data1_bias(value: float) -> dict[str, Any]:
    path = f"{_CH00_DATA_1}/Data_1_BIAS/Value"
    _monitor.set_parameters((path,), (value,))
    return {"message": f"Data_1 Bias = {value}"}


@action("CH00 Data_1 Read RAW", "Read raw signal value for CH00 Data_1")
def ch00_data1_read_raw() -> dict[str, Any]:
    path = f"{_CH00_DATA_1}/Data_1_RAW/port1"
    return _monitor.read_signals((path,))


# ---------------------------------------------------------------------------
# Action registry
# ---------------------------------------------------------------------------

_actions = [
    get_status,
    set_poll_interval,
    list_signals,
    read_signal,
    set_signal,
    list_parameters,
    read_parameter,
    set_parameter,
    list_variables,
    read_variable,
    set_variable,
    ch00_data1_enable,
    ch00_data1_gain,
    ch00_data1_bias,
    ch00_data1_read_raw,
]
