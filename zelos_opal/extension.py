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
    sanitize_name,
)

logger = logging.getLogger(__name__)

_ACQ_GROUP = 0
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

        self._dyn_signals: list[SignalInfo] = []
        self._acq_mapping: list[tuple[str, str]] = []
        self._acq_ready = False
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
        self._teardown_acquisition()
        try:
            self._bridge.disconnect()
        except Exception:
            logger.exception("Error disconnecting from RT-LAB")

    def run(self) -> None:
        """Acquisition loop — streams RT-LAB signals to Zelos traces."""
        acq_time_step = self.config.get("acq_time_step_ms", 1) / 1000
        poll = self.config.get("poll_interval", 1.0)
        prev_state: ModelState | None = None

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
                prev_state = self.model_state
                continue

            if prev_state != ModelState.RUNNING:
                logger.info("Model entered RUNNING — setting up acquisition")
                prev_state = self.model_state

            if not self._acq_ready:
                self._setup_acquisition()
                if not self._acq_ready:
                    self._sleep_with_drain(poll)
                    continue

            try:
                frame = self._bridge.acquire(_ACQ_GROUP, acq_time_step)
            except Exception:
                logger.exception("Acquisition error")
                self._sleep_with_drain(poll)
                continue

            by_event: dict[str, dict[str, float]] = {}
            for i, (evt, fld) in enumerate(self._acq_mapping):
                if i < len(frame.signal_values):
                    by_event.setdefault(evt, {})[fld] = frame.signal_values[i]
            for evt, data in by_event.items():
                self.source.log(evt, data)

            if frame.end_frame:
                self._sleep_with_drain(poll)

    # ------------------------------------------------------------------
    # Discovery & acquisition setup
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Query the bridge for all model contents and build the trace schema.

        Only DYNAMIC signals are included in the acquisition mapping — those are
        the signals that ``SetDynSignalListForGroup`` loads into the acquisition
        group.  ACQUISITION signals are pre-configured in the console subsystem
        and CONTROL signals are accessed via ``GetSignalsByName``.
        """
        self.signal_infos = self._bridge.get_signals_description()
        self.control_signal_infos = self._bridge.get_control_signals_description()
        self.param_infos = self._bridge.get_parameters_description()

        self._dyn_signals = [s for s in self.signal_infos if s.signal_type == SignalType.DYNAMIC]

        all_paths = [s.path for s in self._dyn_signals]
        prefix = _common_prefix(all_paths)

        event_fields: dict[str, dict[str, None]] = {}
        self._acq_mapping = []

        for s in self._dyn_signals:
            for elem in range(1, s.num_elements + 1):
                evt, fld = _split_signal_path(s.path, prefix)
                if s.num_elements > 1:
                    fld = f"{fld}_{elem}"
                base = fld
                idx = 1
                while fld in event_fields.get(evt, {}):
                    fld = f"{base}_{idx}"
                    idx += 1
                event_fields.setdefault(evt, {})[fld] = None
                self._acq_mapping.append((evt, fld))

        for evt, fields in event_fields.items():
            meta = [
                zelos_sdk.TraceEventFieldMetadata(f, zelos_sdk.DataType.Float64) for f in fields
            ]
            self.source.add_event(evt, meta)

        logger.info(
            "Discovered %d signals (%d dynamic), %d control signals, "
            "%d parameters, %d trace events",
            len(self.signal_infos),
            len(self._dyn_signals),
            len(self.control_signal_infos),
            len(self.param_infos),
            len(event_fields),
        )

    def _setup_acquisition(self) -> None:
        """Configure dynamic acquisition for all discovered DYNAMIC signals.

        Follows the ``dynamic_acq`` example pattern: take acquisition control,
        set the capacity, then load every signal by ``(id, element)`` pairs.
        The Python API uses 1-based element indexing (C API uses 0-based).
        """
        if not self._dyn_signals:
            return
        flat: list[int] = []
        for s in self._dyn_signals:
            for elem in range(1, s.num_elements + 1):
                flat.extend([s.subsystem_id, elem])
        num_entries = len(flat) // 2
        try:
            self._bridge.setup_dynamic_acquisition(_ACQ_GROUP, tuple(flat), num_entries)
            self._acq_ready = True
            logger.info("Dynamic acquisition configured with %d signals", num_entries)
        except Exception:
            logger.warning("Failed to set up dynamic acquisition", exc_info=True)
            self._acq_ready = False

    def _teardown_acquisition(self) -> None:
        """Release acquisition control if held."""
        if not self._acq_ready:
            return
        try:
            self._bridge.teardown_dynamic_acquisition(_ACQ_GROUP)
        except Exception:
            logger.warning("Failed to release acquisition control", exc_info=True)
        self._acq_ready = False
