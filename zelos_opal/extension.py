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
from collections.abc import Iterator
from contextlib import contextmanager
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

_TRANSITIONAL_STATES = frozenset(
    {
        ModelState.COMPILING,
        ModelState.LOADING,
        ModelState.RESETTING,
    }
)

_MIN_POLL_INTERVAL = 1.0
_DEFAULT_PARAM_CHUNK_SIZE = 100


@contextmanager
def _timed(name: str) -> Iterator[None]:
    """Log ``name`` before/after a block, reporting elapsed wall time.

    Used to attribute poll-cycle latency to specific RT-LAB API calls so we
    can distinguish slow RT-LAB behaviour from other sources of overrun.
    """
    logger.debug("%s: start", name)
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.debug("%s: done in %.1f ms", name, elapsed_ms)


def _split_signal_path(path: str) -> tuple[str, str]:
    """Split a hierarchical path into ``(event_name, field_name)``.

    All-but-last segments → event (``/``-joined), last segment → field.
    Works for both signal and parameter paths.
    """
    parts = [p for p in path.split("/") if p]
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
        self._trace_params: list[tuple[str, str, str]] = []  # (path, event, field)
        # Fast lookup: api_path -> (event, field) for trace-log on read-back.
        self._param_trace_by_path: dict[str, tuple[str, str]] = {}
        # Fast lookup: control-signal path -> (event, field) for trace-log on
        # write read-back (set_signals async confirmation).  Read-back uses
        # GetSignalsByName since set ops are small-scope (1–few signals).
        self._control_signal_readback: dict[str, tuple[str, str]] = {}
        # Per-type grouped id lists for GetSignalsById.  One entry per
        # SignalType we trace: (signal_type, ids, [(event, field), ...]).
        # Built in _discover(); consumed in run().
        self._signal_read_groups: list[
            tuple[SignalType, tuple[int, ...], list[tuple[str, str]]]
        ] = []
        # Rolling param-read chunks.  Each entry is (names, [(event, field), ...]).
        # Each main poll cycle reads ONE chunk and advances; the full parameter
        # set refreshes over ``len(chunks) * poll_interval`` seconds.  This keeps
        # signal-trace cadence independent of total parameter count.
        self._param_chunks: list[tuple[tuple[str, ...], list[tuple[str, str]]]] = []
        self._param_chunk_idx = 0
        # Per-chunk latency samples for the current sweep — emitted as a summary
        # when the chunk index wraps back to 0, then cleared.  Lets users tune
        # ``param_chunk_size`` from real workload data.
        self._chunk_latencies_ms: list[float] = []
        self._sweep_start: float = 0.0
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
            "param_chunk_size": max(
                int(self.config.get("param_chunk_size", _DEFAULT_PARAM_CHUNK_SIZE)), 1
            ),
            "param_chunk_count": len(self._param_chunks),
        }

    def read_signals(self, names: tuple[str, ...]) -> dict[str, float]:
        values = self._dispatch(self._bridge.get_signals_by_name, names)
        return dict(zip(names, values, strict=False))

    def set_signals(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        """Write control signals; queue a read-back + trace-log asynchronously.

        The SET itself is dispatched synchronously so errors surface to the
        caller.  The subsequent GET (read-back) is queued fire-and-forget —
        the action returns without paying its round-trip latency, and the
        trace picks up the confirmed value on the next drain.
        """

        def _set_impl() -> None:
            self._bridge.acquire_signal_control(1)
            try:
                self._bridge.set_signals_by_name(names, values)
            finally:
                self._bridge.release_signal_control(1)

        self._dispatch(_set_impl)
        self._dispatch_async(self._readback_and_log_signals, names)

    def read_parameters(self, names: tuple[str, ...]) -> dict[str, float]:
        values = self._dispatch(self._bridge.get_parameters_by_name, names)
        return dict(zip(names, values, strict=False))

    def set_parameters(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        """Write parameters; queue a read-back + trace-log asynchronously.

        Same pattern as ``set_signals``: SET blocks the caller just long
        enough to confirm the write landed, but the GET read-back runs
        later on the main thread so a slow bulk-param-chunk read can't
        extend action latency.
        """

        def _set_impl() -> None:
            self._bridge.acquire_parameter_control()
            try:
                self._bridge.set_parameters_by_name(names, values)
            finally:
                self._bridge.release_parameter_control()

        self._dispatch(_set_impl)
        self._dispatch_async(self._readback_and_log_params, names)

    def _readback_and_log_params(self, names: tuple[str, ...]) -> None:
        """Runs on the main thread via ``_dispatch_async``."""
        values = self._bridge.get_parameters_by_name(names)
        by_event: dict[str, dict[str, float]] = {}
        for name, val in zip(names, values, strict=False):
            mapping = self._param_trace_by_path.get(name)
            if mapping is None:
                continue
            evt, fld = mapping
            by_event.setdefault(evt, {})[fld] = val
        for evt, data in by_event.items():
            self.source.log(evt, data)

    def _readback_and_log_signals(self, names: tuple[str, ...]) -> None:
        """Runs on the main thread via ``_dispatch_async``.

        Uses ``GetSignalsByName`` since set-readback scope is typically 1–few
        signals; the per-call name resolution cost is negligible at that size
        and avoids grouping by signal-type.  Unknown paths are skipped.
        """
        paths = [n for n in names if n in self._control_signal_readback]
        if not paths:
            return
        try:
            values = self._bridge.get_signals_by_name(tuple(paths))
        except Exception:
            logger.exception("Signal read-back failed")
            return

        by_event: dict[str, dict[str, float]] = {}
        for path, val in zip(paths, values, strict=False):
            evt, fld = self._control_signal_readback[path]
            by_event.setdefault(evt, {})[fld] = val
        for evt, data in by_event.items():
            self.source.log(evt, data)

    def read_variable(self, name: str) -> dict[str, Any]:
        infos = self._dispatch(self._bridge.get_variables_description)
        for v in infos:
            if v.name == name:
                return {name: v.value}
        return {"error": f"Variable not found: {name}"}

    def set_variable(self, name: str, value: float) -> dict[str, str]:
        var = next((v for v in self.variable_infos if v.name == name), None)
        if var is None:
            return {"error": f"Variable not found: {name}"}
        self._dispatch(self._bridge.set_variable, var.var_id, value)
        return {"message": f"Set {name} = {value}"}

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

    def _dispatch_async(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        """Submit *fn* to the main thread fire-and-forget.

        Intended for side-effect-only work (e.g. read-back + trace-log after
        a set) where the caller shouldn't pay the round-trip latency.
        Exceptions are logged, not propagated.
        """

        def _wrapper() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Async dispatch failed: %s", getattr(fn, "__name__", repr(fn)))

        event = threading.Event()
        container: dict[str, Any] = {"result": None, "error": None}
        self._cmd_queue.put((_wrapper, (), {}, event, container))

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
        """Sleep while remaining responsive to queued commands.

        Enforces ``_MIN_POLL_INTERVAL`` as an absolute floor — this is the
        last line of defence against any caller passing a zero/negative
        duration and turning the poll loop into a hot retry spin.
        """
        seconds = max(seconds, _MIN_POLL_INTERVAL)
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
        """Connect to RT-LAB and discover the model.

        A failure to connect (``OpenProject``) is non-recoverable and is
        allowed to propagate so the extension exits with a clear error.
        Discovery is best-effort — partial data is still useful.
        """
        logger.info("Starting OPAL-RT monitor")
        self.running = True
        self._bridge.connect(self.config.get("project_path", ""))
        try:
            self._discover()
        except Exception:
            logger.exception("Discovery failed (continuing with partial data)")

    def stop(self) -> None:
        logger.info("Stopping OPAL-RT monitor")
        self.running = False
        try:
            self._bridge.disconnect()
        except Exception:
            logger.exception("Error disconnecting from RT-LAB")

    def run(self) -> None:
        """Poll loop — reads RT-LAB signals and parameters, streams to Zelos.

        Signals are read in full each cycle via ``GetSignalsById`` (cheap).
        Parameters are read **one chunk per cycle** via ``GetParametersByName``
        so a slow bulk-param read never extends signal cadence.  Each chunk
        is timed and a summary is logged at the end of every full sweep —
        tune ``param_chunk_size`` from that data.
        """
        prev_state: ModelState | None = None

        while self.running:
            poll = max(self.config.get("poll_interval", 1.0), _MIN_POLL_INTERVAL)
            self._drain_commands()

            with _timed("get_model_state"):
                self.model_state = self._bridge.get_model_state()
            self.source.model_info.log(
                state=self.model_state.value,
                signal_count=len(self.signal_infos),
                parameter_count=len(self.param_infos),
            )

            if self.model_state != prev_state:
                logger.info("Model state: %s", self.model_state.name)
                prev_state = self.model_state

            by_event: dict[str, dict[str, float]] = {}
            param_safe = self.model_state not in _TRANSITIONAL_STATES

            if self._param_chunks and param_safe:
                self._read_next_param_chunk(by_event)

            if self.model_state == ModelState.RUNNING and self._signal_read_groups:
                for sig_type, ids, mapping in self._signal_read_groups:
                    try:
                        with _timed(f"get_signals_by_id {sig_type.name} ({len(ids)} signals)"):
                            values = self._bridge.get_signals_by_id(sig_type.value, ids)
                        for (evt, fld), val in zip(mapping, values, strict=False):
                            by_event.setdefault(evt, {})[fld] = val
                    except Exception:
                        logger.exception("Signal read error for %s", sig_type.name)

            for evt, data in by_event.items():
                self.source.log(evt, data)

            self._sleep_with_drain(poll)

    def _read_next_param_chunk(self, by_event: dict[str, dict[str, float]]) -> None:
        """Read one chunk from ``self._param_chunks`` and advance the index.

        Records per-chunk latency; on wrap-around, emits a sweep summary so
        operators can pick a ``param_chunk_size`` that balances responsive
        signal cadence (small chunks) against full-refresh latency (large
        chunks).
        """
        total = len(self._param_chunks)
        idx = self._param_chunk_idx
        names, mapping = self._param_chunks[idx]

        if idx == 0:
            self._sweep_start = time.perf_counter()

        t0 = time.perf_counter()
        try:
            with _timed(f"param chunk [{idx + 1}/{total}] ({len(names)} names)"):
                values = self._bridge.get_parameters_by_name(names)
            for (evt, fld), val in zip(mapping, values, strict=False):
                by_event.setdefault(evt, {})[fld] = val
        except Exception:
            logger.exception("Parameter chunk %d read error", idx)
        chunk_ms = (time.perf_counter() - t0) * 1000
        self._chunk_latencies_ms.append(chunk_ms)

        self._param_chunk_idx = (idx + 1) % total
        if self._param_chunk_idx == 0:
            self._emit_sweep_summary()

    def _emit_sweep_summary(self) -> None:
        """Log aggregate chunk latency + full-sweep wall time, then clear."""
        lats = self._chunk_latencies_ms
        if not lats:
            return
        sweep_ms = (time.perf_counter() - self._sweep_start) * 1000
        n = len(lats)
        avg = sum(lats) / n
        mn = min(lats)
        mx = max(lats)
        sorted_lats = sorted(lats)
        p50 = sorted_lats[n // 2]
        p95 = sorted_lats[max(0, int(round(0.95 * (n - 1))))]
        logger.info(
            "param sweep complete: %d chunks, total params=%d, wall=%.1fs, "
            "chunk_ms avg=%.1f p50=%.1f p95=%.1f min=%.1f max=%.1f",
            n,
            sum(len(names) for names, _ in self._param_chunks),
            sweep_ms / 1000,
            avg,
            p50,
            p95,
            mn,
            mx,
        )
        self._chunk_latencies_ms = []

    # ------------------------------------------------------------------
    # Discovery & acquisition setup
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Query the bridge for all model contents and build the trace schema.

        All signal types we can read (DYNAMIC + ACQUISITION + CONTROL) and
        parameters are traced.  Control signals ride the same ID-grouped
        periodic read as the rest; the async read-back after ``set_signals``
        logs to the same (evt, field) so the stream stays continuous.

        Parameters are split into fixed-size chunks (``param_chunk_size``)
        for rolling reads in ``run()`` — see ``_read_next_param_chunk``.
        """
        self.signal_infos = self._bridge.get_signals_description()
        self.control_signal_infos = self._bridge.get_control_signals_description()
        self.param_infos = self._bridge.get_parameters_description()
        self.variable_infos = self._bridge.get_variables_description()

        # --- diagnostics: signal type breakdown ---
        from collections import Counter

        type_counts = Counter(s.signal_type for s in self.signal_infos)
        logger.info(
            "GetSignalsDescription: %d total — %s",
            len(self.signal_infos),
            ", ".join(f"{t.name}={c}" for t, c in sorted(type_counts.items())) or "empty",
        )
        logger.info("GetControlSignalsDescription: %d total", len(self.control_signal_infos))
        logger.info("GetParametersDescription: %d total", len(self.param_infos))
        logger.info("GetVariablesDescription: %d total", len(self.variable_infos))

        traced_types = (SignalType.DYNAMIC, SignalType.ACQUISITION, SignalType.CONTROL)
        output_signals = [s for s in self.signal_infos if s.signal_type in traced_types]
        skipped_signals = [s for s in self.signal_infos if s.signal_type not in traced_types]

        if skipped_signals:
            skip_counts = Counter(s.signal_type for s in skipped_signals)
            logger.info(
                "Skipping %d signal(s) from tracing (unsupported type): %s",
                len(skipped_signals),
                ", ".join(f"{t.name}={c}" for t, c in sorted(skip_counts.items())),
            )
            for s in skipped_signals:
                logger.debug(
                    "  skipped signal: type=%s path=%r name=%r",
                    s.signal_type.name,
                    s.path,
                    s.name,
                )

        event_fields: dict[str, dict[str, None]] = {}
        self._trace_signals = []
        self._trace_params = []
        self._param_trace_by_path = {}
        self._control_signal_readback = {}
        self._param_chunks = []
        self._param_chunk_idx = 0
        self._chunk_latencies_ms = []
        grouped: dict[SignalType, tuple[list[int], list[tuple[str, str]]]] = {}

        def _reserve_field(evt: str, base: str) -> str:
            fld = base
            n = 1
            while fld in event_fields.get(evt, {}):
                fld = f"{base}_{n}"
                n += 1
            event_fields.setdefault(evt, {})[fld] = None
            return fld

        for s in output_signals:
            evt, base = _split_signal_path(s.path)
            fld = _reserve_field(evt, base)
            self._trace_signals.append((s.path, evt, fld))
            ids, mapping = grouped.setdefault(s.signal_type, ([], []))
            ids.append(s.signal_id)
            mapping.append((evt, fld))
            if s.signal_type == SignalType.CONTROL:
                self._control_signal_readback[s.path] = (evt, fld)

        self._signal_read_groups = [
            (sig_type, tuple(ids), mapping) for sig_type, (ids, mapping) in grouped.items()
        ]

        chunk_params: list[tuple[str, tuple[str, str]]] = []
        for p in self.param_infos:
            api_path = f"{p.path}/{p.name}"
            evt, base = _split_signal_path(api_path)
            fld = _reserve_field(evt, base)
            self._trace_params.append((api_path, evt, fld))
            self._param_trace_by_path[api_path] = (evt, fld)
            chunk_params.append((api_path, (evt, fld)))

        for evt, fields in event_fields.items():
            meta = [
                zelos_sdk.TraceEventFieldMetadata(f, zelos_sdk.DataType.Float64) for f in fields
            ]
            self.source.add_event(evt, meta)

        chunk_size = max(int(self.config.get("param_chunk_size", _DEFAULT_PARAM_CHUNK_SIZE)), 1)
        for i in range(0, len(chunk_params), chunk_size):
            slab = chunk_params[i : i + chunk_size]
            names = tuple(api_path for api_path, _ in slab)
            mapping = [evt_fld for _, evt_fld in slab]
            self._param_chunks.append((names, mapping))

        logger.info(
            "Tracing %d/%d signals, %d/%d parameters (%d chunks of <=%d), %d trace events",
            len(self._trace_signals),
            len(self.signal_infos),
            len(self._trace_params),
            len(self.param_infos),
            len(self._param_chunks),
            chunk_size,
            len(event_fields),
        )
