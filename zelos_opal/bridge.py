"""Thin wrapper around the native RtlabApi module.

Handles import resolution and exposes typed return values.
All RT-LAB API calls are routed through this single class.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from zelos_opal.constants import (
    ModelState,
    ParameterInfo,
    SignalInfo,
    SignalType,
    VariableInfo,
)

logger = logging.getLogger(__name__)

_RTLAB_DEFAULT_ROOT = r"C:\OPAL-RT\RT-LAB"


def _ensure_rtlab_importable(rtlab_path: str | None = None) -> None:
    """Make sure ``import RtlabApi`` will succeed, or raise with a clear message.

    Resolution order:
    1. Direct import (works when PYTHONPATH is already set).
    2. If *rtlab_path* is given, add ``<rtlab_path>/common/python`` and
       ``<rtlab_path>/common/bin`` to ``sys.path`` and retry.
    3. Auto-discover from ``C:\\OPAL-RT\\RT-LAB\\v*`` (latest version wins).
    4. Raise ``RuntimeError`` with actionable guidance.
    """
    try:
        import RtlabApi  # noqa: F401

        return
    except Exception:
        pass

    candidates: list[str] = []

    if rtlab_path:
        candidates.append(rtlab_path)
    else:
        versions = sorted(Path(_RTLAB_DEFAULT_ROOT).glob("v*"), reverse=True)
        candidates.extend(versions)

    for base in candidates:
        py_dir = str(Path(base) / "common" / "python")
        bin_dir = str(Path(base) / "common" / "bin")
        for d in (py_dir, bin_dir):
            if d not in sys.path:
                sys.path.insert(0, d)
        try:
            import RtlabApi  # noqa: F401

            logger.info("Loaded RtlabApi from %s", base)
            return
        except Exception:
            continue

    raise RuntimeError(
        "RtlabApi could not be imported. Ensure RT-LAB is installed and either:\n"
        "  - Set PYTHONPATH to include <RT-LAB>/common/python and <RT-LAB>/common/bin\n"
        f"  - Or install RT-LAB under {_RTLAB_DEFAULT_ROOT} (auto-discovered)\n"
        "  - Or set 'rtlab_path' in the extension config to your RT-LAB version directory"
    )


class LiveBridge:
    """Wraps the native RtlabApi module for communication with a real RT-LAB target."""

    def __init__(self, rtlab_path: str | None = None) -> None:
        _ensure_rtlab_importable(rtlab_path)
        import RtlabApi as _api

        self._api: Any = _api
        self._connected = False

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, project_path: str) -> None:
        self._api.OpenProject(project_path)
        self._connected = True
        logger.info("Connected to RT-LAB project: %s", project_path)

    def disconnect(self) -> None:
        if self._connected:
            try:
                self._api.CloseProject()
            except Exception:
                logger.exception("Error closing RT-LAB project")
            self._connected = False
            logger.info("Disconnected from RT-LAB project")

    def get_model_state(self) -> ModelState:
        state, _ = self._api.GetModelState()
        try:
            return ModelState(int(state))
        except (ValueError, TypeError):
            return ModelState.DISCONNECTED

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def get_signals_description(self) -> list[SignalInfo]:
        raw = self._api.GetSignalsDescription()
        return [
            SignalInfo(
                signal_type=SignalType.from_raw(sig[0]),
                signal_id=sig[1],
                path=sig[2],
                name=sig[3],
                label=sig[4],
            )
            for sig in raw
        ]

    def get_signals_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._api.GetSignalsByName(names))

    def set_signals_by_name(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        self._api.SetSignalsByName(names, values)

    def acquire_signal_control(self, subsystem_id: int) -> None:
        self._api.GetSignalControl(subsystem_id, 1)

    def release_signal_control(self, subsystem_id: int) -> None:
        self._api.GetSignalControl(subsystem_id, 0)

    # ------------------------------------------------------------------
    # Control signals
    # ------------------------------------------------------------------

    def get_control_signals_description(self) -> list[SignalInfo]:
        raw = self._api.GetControlSignalsDescription()
        return [
            SignalInfo(
                signal_type=SignalType.CONTROL,
                signal_id=sig[1],
                path=sig[2],
                name=sig[3],
                label=sig[4],
            )
            for sig in raw
        ]

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    def get_parameters_description(self) -> list[ParameterInfo]:
        raw = self._api.GetParametersDescription()
        return [
            ParameterInfo(param_id=p[0], path=p[1], name=p[2], variable=p[3], value=p[4])
            for p in raw
        ]

    def get_parameters_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._api.GetParametersByName(names))

    def set_parameters_by_name(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        self._api.SetParametersByName(names, values)

    def acquire_parameter_control(self) -> None:
        self._api.GetParameterControl(1)

    def release_parameter_control(self) -> None:
        self._api.GetParameterControl(0)

    # ------------------------------------------------------------------
    # Variables (MATLAB workspace)
    # ------------------------------------------------------------------

    def get_variables_description(self) -> list[VariableInfo]:
        try:
            raw = self._api.GetVariablesDescription()
            return [VariableInfo(var_id=v[0], name=v[1], value=float(v[2])) for v in raw]
        except Exception:
            logger.warning("GetVariablesDescription not available for this model", exc_info=True)
            return []

    def set_variable(self, var_id: int, value: float) -> None:
        self._api.SetVariables(((var_id, value),))
