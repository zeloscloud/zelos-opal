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
    * All-but-last remaining segments → event (underscore-joined).
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
    event = "_".join(sanitize_name(p) for p in parts[:-1])
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

        self._group_mapping: dict[int, list[tuple[str, str]]] = {}
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
        names = [s.path for s in self.control_signal_infos]
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
            self.source.model_info.log(
                state=self.model_state.value,
                signal_count=len(self.signal_infos),
                parameter_count=len(self.param_infos),
            )
            if self.model_state != ModelState.RUNNING:
                logger.info(
                    "Model state is %s — waiting for RUNNING",
                    self.model_state.name,
                )
                self._sleep_with_drain(poll)
                if not self._group_mapping:
                    self._discover()
                continue

            any_end_frame = False
            for group, mapping in self._group_mapping.items():
                try:
                    frame = self._bridge.acquire(group, acq_time_step)
                except Exception:
                    logger.exception("Acquisition error for group %d", group)
                    continue

                by_event: dict[str, dict[str, float]] = {}
                for i, (evt, fld) in enumerate(mapping):
                    if i < len(frame.signal_values):
                        by_event.setdefault(evt, {})[fld] = frame.signal_values[i]
                for evt, data in by_event.items():
                    getattr(self.source, evt).log(**data)
                any_end_frame = any_end_frame or frame.end_frame

            if any_end_frame or not self._group_mapping:
                self._sleep_with_drain(poll)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Query the bridge for all model contents and build the trace schema."""
        self.signal_infos = self._bridge.get_signals_description()
        self.control_signal_infos = self._bridge.get_control_signals_description()
        self.param_infos = self._bridge.get_parameters_description()
        self.variable_infos = self._bridge.get_variables_description()

        raw_groups: dict[int, list[str]] = {}
        for group in range(1, 17):
            names = self._bridge.get_signal_names_for_group(group)
            if names:
                raw_groups[group] = names

        if not raw_groups and self.signal_infos:
            raw_groups[1] = [s.path for s in self.signal_infos]

        all_paths = [p for paths in raw_groups.values() for p in paths]
        prefix = _common_prefix(all_paths)

        # Build (event, field) mapping per acq group; deduplicate per event.
        event_fields: dict[str, dict[str, None]] = {}
        self._group_mapping = {}

        for group, paths in raw_groups.items():
            mapping: list[tuple[str, str]] = []
            for path in paths:
                evt, fld = _split_signal_path(path, prefix)
                base = fld
                idx = 1
                while fld in event_fields.get(evt, {}):
                    fld = f"{base}_{idx}"
                    idx += 1
                event_fields.setdefault(evt, {})[fld] = None
                mapping.append((evt, fld))
            self._group_mapping[group] = mapping

        for evt, fields in event_fields.items():
            self.source.add_event(
                evt,
                [zelos_sdk.TraceEventFieldMetadata(f, zelos_sdk.DataType.Float64) for f in fields],
            )

        logger.info(
            "Discovered %d signals, %d control signals, %d parameters, %d variables "
            "in %d acq group(s), %d trace events",
            len(self.signal_infos),
            len(self.control_signal_infos),
            len(self.param_infos),
            len(self.variable_infos),
            len(raw_groups),
            len(event_fields),
        )
