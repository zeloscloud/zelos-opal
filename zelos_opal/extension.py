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
import os.path
import queue
import re
import threading
import time
from typing import Any

import zelos_sdk

from zelos_opal.bridge import LiveBridge
from zelos_opal.constants import (
    ModelState,
    ParameterInfo,
    SignalInfo,
    SignalType,
    VariableInfo,
    sanitize_name,
)

logger = logging.getLogger(__name__)

_PORT_RE = re.compile(r"^port\d+$", re.IGNORECASE)


def _common_prefix(paths: list[str]) -> str:
    """Longest common directory prefix across *paths*."""
    if not paths:
        return ""
    prefix = os.path.commonprefix(paths)
    slash = prefix.rfind("/")
    return prefix[: slash + 1] if slash >= 0 else ""


def _split_signal_path(path: str, prefix: str) -> tuple[str, str]:
    """Split a signal path into ``(event_name, field_name)``.

    * Strips the shared model prefix and any trailing ``portN``.
    * All-but-last remaining segments → event (``/``-joined, matching OPAL-RT hierarchy).
    * Last segment → field.
    """
    remainder = path[len(prefix) :] if path.startswith(prefix) else path
    parts = [p for p in remainder.split("/") if p]
    if parts and _PORT_RE.match(parts[-1]):
        parts = parts[:-1]
    if not parts:
        return ("signals", sanitize_name(path))
    if len(parts) == 1:
        return ("signals", sanitize_name(parts[0]))
    event = "/".join(sanitize_name(p) for p in parts[:-1])
    field = sanitize_name(parts[-1])
    return (event, field)


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

        self._trace_signals: list[tuple[str, str, str]] = []  # (path, event, field)
        self._cmd_queue: queue.Queue[tuple[Any, tuple, dict, threading.Event, dict]] = queue.Queue()

        self.source = zelos_sdk.TraceSourceCacheLast("opal")
        self.source.add_event(
            "model_info",
            [
                zelos_sdk.TraceEventFieldMetadata("state", zelos_sdk.DataType.UInt8),
                zelos_sdk.TraceEventFieldMetadata("signal_count", zelos_sdk.DataType.Int64),
                zelos_sdk.TraceEventFieldMetadata("parameter_count", zelos_sdk.DataType.Int64),
            ],
        )
        self.source.add_value_table(
            "model_info",
            "state",
            {s.value: s.name for s in ModelState},
        )
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

    def read_variable(self, name: str) -> dict[str, float]:
        infos = self._dispatch(self._bridge.get_variables_description)
        for v in infos:
            if v.name == name:
                return {name: v.value}
        raise ValueError(f"Variable not found: {name}")

    def set_variable(self, name: str, value: float) -> None:
        var = next((v for v in self.variable_infos if v.name == name), None)
        if var is None:
            raise ValueError(f"Variable not found: {name}")
        self._dispatch(self._bridge.set_variable, var.var_id, value)

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
        """Poll loop — reads RT-LAB signals by name and streams to Zelos traces."""
        prev_state: ModelState | None = None
        names = tuple(path for path, _, _ in self._trace_signals)

        while self.running:
            poll = self.config.get("poll_interval", 1.0)
            self._drain_commands()
            self.model_state = self._bridge.get_model_state()
            self.source.model_info.log(
                state=self.model_state.value,
                signal_count=len(self.signal_infos),
                parameter_count=len(self.param_infos),
            )
            if self.model_state != ModelState.RUNNING:
                if prev_state != self.model_state:
                    logger.info("Model state is %s — waiting for RUNNING", self.model_state.name)
                self._sleep_with_drain(poll)
                prev_state = self.model_state
                continue

            if prev_state != ModelState.RUNNING:
                logger.info("Model entered RUNNING — polling signals")
                prev_state = self.model_state

            if not names:
                self._sleep_with_drain(poll)
                continue

            try:
                values = self._bridge.get_signals_by_name(names)
            except Exception:
                logger.exception("Signal read error")
                self._sleep_with_drain(poll)
                continue

            by_event: dict[str, dict[str, float]] = {}
            for (_, evt, fld), val in zip(self._trace_signals, values, strict=False):
                by_event.setdefault(evt, {})[fld] = val
            for evt, data in by_event.items():
                self.source.log(evt, data)

            self._sleep_with_drain(poll)

    # ------------------------------------------------------------------
    # Discovery & acquisition setup
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Query the bridge for all model contents and build the trace schema.

        All output signals (DYNAMIC + ACQUISITION) are included for tracing via
        ``GetSignalsByName`` polling.  CONTROL signals are omitted from tracing
        (they are model inputs) but remain available through actions.
        """
        self.signal_infos = self._bridge.get_signals_description()
        self.control_signal_infos = self._bridge.get_control_signals_description()
        self.param_infos = self._bridge.get_parameters_description()
        self.variable_infos = self._bridge.get_variables_description()

        traced_types = (SignalType.DYNAMIC, SignalType.ACQUISITION)
        output_signals = [s for s in self.signal_infos if s.signal_type in traced_types]

        all_paths = [s.path for s in output_signals]
        prefix = _common_prefix(all_paths)

        event_fields: dict[str, dict[str, None]] = {}
        self._trace_signals = []

        for s in output_signals:
            evt, fld = _split_signal_path(s.path, prefix)
            base = fld
            idx = 1
            while fld in event_fields.get(evt, {}):
                fld = f"{base}_{idx}"
                idx += 1
            event_fields.setdefault(evt, {})[fld] = None
            self._trace_signals.append((s.path, evt, fld))

        for evt, fields in event_fields.items():
            meta = [
                zelos_sdk.TraceEventFieldMetadata(f, zelos_sdk.DataType.Float64) for f in fields
            ]
            self.source.add_event(evt, meta)

        logger.info(
            "Discovered %d signals (%d traced), %d control signals, %d parameters, %d trace events",
            len(self.signal_infos),
            len(output_signals),
            len(self.control_signal_infos),
            len(self.param_infos),
            len(event_fields),
        )
