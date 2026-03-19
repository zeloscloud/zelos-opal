"""OPAL-RT monitor — lifecycle, tracing, and thread-safe API access.

The RT-LAB C API is thread-bound: all calls must happen on the same OS thread
that called ``OpenProject()``.  The Zelos SDK dispatches action handlers on a
Tokio worker thread, so any live API call from an action must be routed back
to the main thread.

This module owns that mechanism (``_dispatch`` / ``_drain_commands``).  Public
accessor methods (``read_signals``, ``set_parameters``, …) are safe to call
from any thread — actions use them without knowing about the plumbing.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any

import zelos_sdk

from zelos_opal.bridge import LiveBridge
from zelos_opal.constants import (
    ModelState,
    ParameterInfo,
    SignalInfo,
    VariableInfo,
    sanitize_name,
)

logger = logging.getLogger(__name__)


class OpalMonitor:
    """Connects to an OPAL-RT target, discovers model contents, and streams signals."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.running = False

        self.model_state: ModelState = ModelState.DISCONNECTED
        self.signal_infos: list[SignalInfo] = []
        self.control_signal_infos: list[SignalInfo] = []
        self.param_infos: list[ParameterInfo] = []
        self.variable_infos: list[VariableInfo] = []

        self._group_fields: dict[int, list[str]] = {}
        self._cmd_queue: queue.Queue[tuple[Any, tuple, dict, threading.Event, dict]] = queue.Queue()

        self.source = zelos_sdk.TraceSourceCacheLast("opal")
        self._define_monitoring_schema()

        self._bridge = LiveBridge(rtlab_path=config.get("rtlab_path"))

    # ------------------------------------------------------------------
    # Thread-safe accessors (called by action handlers)
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "model_state": self.model_state.name,
            "signal_count": len(self.signal_infos),
            "control_signal_count": len(self.control_signal_infos),
            "parameter_count": len(self.param_infos),
            "variable_count": len(self.variable_infos),
            "poll_interval": self.config.get("poll_interval", 1.0),
        }

    def read_signals(self, names: tuple[str, ...]) -> dict[str, float]:
        values = self._dispatch(self._bridge.get_signals_by_name, names)
        return dict(zip(names, values, strict=False))

    def set_signals(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        def _impl() -> None:
            self._bridge.acquire_signal_control(1)
            try:
                self._bridge.set_signals_by_name(names, values)
            finally:
                self._bridge.release_signal_control(1)

        self._dispatch(_impl)

    def read_control_signals(self) -> dict[str, float]:
        if not self.control_signal_infos:
            return {}
        values = self._dispatch(self._bridge.get_control_signals)
        names = [s.name for s in self.control_signal_infos]
        return dict(zip(names, values, strict=False))

    def set_control_signals(self, subsystem_id: int, values: tuple[float, ...]) -> None:
        def _impl() -> None:
            self._bridge.acquire_signal_control(subsystem_id)
            try:
                self._bridge.set_control_signals(subsystem_id, values)
            finally:
                self._bridge.release_signal_control(subsystem_id)

        self._dispatch(_impl)

    def read_parameters(self, names: tuple[str, ...]) -> dict[str, float]:
        values = self._dispatch(self._bridge.get_parameters_by_name, names)
        return dict(zip(names, values, strict=False))

    def set_parameters(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        def _impl() -> None:
            self._bridge.acquire_parameter_control()
            try:
                self._bridge.set_parameters_by_name(names, values)
            finally:
                self._bridge.release_parameter_control()

        self._dispatch(_impl)

    def read_variables(self, names: tuple[str, ...]) -> dict[str, float]:
        values = self._dispatch(self._bridge.get_variables_by_name, names)
        return dict(zip(names, values, strict=False))

    def set_variables(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        self._dispatch(self._bridge.set_variables, names, values)

    # ------------------------------------------------------------------
    # Main-thread command dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Submit *fn* to the main thread and block until complete."""
        event = threading.Event()
        container: dict[str, Any] = {"result": None, "error": None}
        self._cmd_queue.put((fn, args, kwargs, event, container))
        event.wait(timeout=30)
        if container["error"] is not None:
            raise container["error"]
        return container["result"]

    def _drain_commands(self) -> None:
        while True:
            try:
                fn, args, kwargs, event, container = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            try:
                container["result"] = fn(*args, **kwargs)
            except Exception as exc:
                container["error"] = exc
            finally:
                event.set()

    def _sleep_with_drain(self, seconds: float) -> None:
        """Sleep while remaining responsive to queued commands."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and self.running:
            self._drain_commands()
            remaining = end - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.05, remaining))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        logger.info("Starting OPAL-RT monitor")
        self.running = True
        self._bridge.connect(self.config.get("project_path", ""))
        self._discover()

    def stop(self) -> None:
        logger.info("Stopping OPAL-RT monitor")
        self.running = False
        try:
            self._bridge.disconnect()
        except Exception:
            logger.exception("Error disconnecting from RT-LAB")

    def run(self) -> None:
        """Acquisition loop — streams RT-LAB signals to Zelos traces."""
        acq_time_step = self.config.get("acq_time_step_ms", 1) / 1000
        poll = self.config.get("poll_interval", 1.0)

        while self.running:
            self._drain_commands()
            self.model_state = self._bridge.get_model_state()
            if self.model_state != ModelState.RUNNING:
                logger.info(
                    "Model state is %s — waiting for RUNNING",
                    self.model_state.name,
                )
                self._sleep_with_drain(poll)
                if not self._group_fields:
                    self._discover()
                continue

            any_end_frame = False
            for group, fields in self._group_fields.items():
                try:
                    frame = self._bridge.acquire(group, acq_time_step)
                except Exception:
                    logger.exception("Acquisition error for group %d", group)
                    continue

                data = {
                    field: frame.signal_values[i]
                    for i, field in enumerate(fields)
                    if i < len(frame.signal_values)
                }
                if data:
                    self.source.signals.log(**data)

                self.source.acq_monitor.log(
                    missed_data=frame.missed_data,
                    sim_time=frame.sim_time,
                    sample_rate=frame.sample_rate,
                )
                any_end_frame = any_end_frame or frame.end_frame

            if any_end_frame or not self._group_fields:
                self._sleep_with_drain(poll)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _define_monitoring_schema(self) -> None:
        self.source.add_event(
            "acq_monitor",
            [
                zelos_sdk.TraceEventFieldMetadata("missed_data", zelos_sdk.DataType.Float64),
                zelos_sdk.TraceEventFieldMetadata("sim_time", zelos_sdk.DataType.Float64, "s"),
                zelos_sdk.TraceEventFieldMetadata("sample_rate", zelos_sdk.DataType.Float64, "Hz"),
            ],
        )

    def _discover(self) -> None:
        """Query the bridge for all model contents and build the trace schema."""
        self.signal_infos = self._bridge.get_signals_description()
        self.control_signal_infos = self._bridge.get_control_signals_description()
        self.param_infos = self._bridge.get_parameters_description()
        self.variable_infos = self._bridge.get_variables_description()

        self._group_fields = {}
        for group in range(1, 17):
            names = self._bridge.get_signal_names_for_group(group)
            if names:
                self._group_fields[group] = [sanitize_name(n) for n in names]

        if not self._group_fields and self.signal_infos:
            self._group_fields[1] = [sanitize_name(s.name) for s in self.signal_infos]

        all_fields = [f for fields in self._group_fields.values() for f in fields]
        if all_fields:
            fields = [
                zelos_sdk.TraceEventFieldMetadata(name, zelos_sdk.DataType.Float64)
                for name in all_fields
            ]
            self.source.add_event("signals", fields)

        logger.info(
            "Discovered %d signals, %d control signals, %d parameters, %d variables "
            "in %d acq group(s)",
            len(self.signal_infos),
            len(self.control_signal_infos),
            len(self.param_infos),
            len(self.variable_infos),
            len(self._group_fields),
        )
