"""Tests for the OPAL-RT extension."""

from zelos_opal.constants import (
    ModelState,
    ParameterInfo,
    SignalInfo,
    SignalType,
    VariableInfo,
    sanitize_name,
)
from zelos_opal.extension import _split_signal_path

# ---------------------------------------------------------------------------
# MockBridge — deterministic stand-in for LiveBridge
# ---------------------------------------------------------------------------


class MockBridge:
    """Deterministic bridge for unit tests."""

    SIGNALS = [
        SignalInfo(name="voltage", path="mock/v", label="Voltage", signal_id=1),
        SignalInfo(name="current", path="mock/i", label="Current", signal_id=2),
        SignalInfo(
            name="switch_pos",
            path="mock/ctrl",
            label="Switch",
            signal_type=SignalType.CONTROL,
            signal_id=100,
        ),
    ]
    CONTROL_SIGNALS = [
        SignalInfo(
            name="switch_pos",
            path="mock/ctrl",
            label="Switch",
            signal_type=SignalType.CONTROL,
            signal_id=100,
        ),
    ]
    PARAMS = [
        ParameterInfo(name="Gain", path="mock/blk", variable="Gain", value=1.0),
    ]

    def __init__(self) -> None:
        self.connected = False
        self.signal_control_held = False
        self.param_control_held = False
        self._signal_values: dict[str, float] = {"mock/v": 120.0, "mock/i": 50.0, "mock/ctrl": 0.0}
        self._param_values: dict[str, float] = {"mock/blk/Gain": 1.0}

    def connect(self, project_path: str) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def get_model_state(self) -> ModelState:
        return ModelState.RUNNING if self.connected else ModelState.DISCONNECTED

    def get_signals_description(self) -> list[SignalInfo]:
        return list(self.SIGNALS)

    def get_signals_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._signal_values.get(n, 0.0) for n in names)

    def get_signals_by_id(self, signal_type: int, signal_ids: tuple[int, ...]) -> tuple[float, ...]:
        """Look up values keyed by (signal_type, signal_id).

        Mirrors RT-LAB's ``GetSignalsById`` contract (single type per call).
        """
        lookup = {
            (int(s.signal_type), s.signal_id): self._signal_values.get(s.path, 0.0)
            for s in self.SIGNALS
        }
        return tuple(lookup.get((signal_type, sid), 0.0) for sid in signal_ids)

    def set_signals_by_name(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        for n, v in zip(names, values, strict=True):
            self._signal_values[n] = v

    def acquire_signal_control(self, subsystem_id: int) -> None:
        self.signal_control_held = True

    def release_signal_control(self, subsystem_id: int) -> None:
        self.signal_control_held = False

    def get_control_signals_description(self) -> list[SignalInfo]:
        return list(self.CONTROL_SIGNALS)

    def get_variables_description(self) -> list[VariableInfo]:
        return []

    def set_variable(self, var_id: int, value: float) -> None:
        pass

    def get_parameters_description(self) -> list[ParameterInfo]:
        return list(self.PARAMS)

    def get_parameters_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._param_values.get(n, 0.0) for n in names)

    def set_parameters_by_name(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        for n, v in zip(names, values, strict=True):
            self._param_values[n] = v

    def acquire_parameter_control(self) -> None:
        self.param_control_held = True

    def release_parameter_control(self) -> None:
        self.param_control_held = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor(bridge=None, **config_overrides):
    """Create an OpalMonitor wired to MockBridge, with dispatch bypassed.

    Both the sync and async dispatch primitives are replaced with direct
    calls so tests see immediate effects — real queuing behaviour is
    tested separately in ``test_dispatch_async_enqueues_without_blocking``.
    """
    from unittest.mock import patch

    from zelos_opal.extension import OpalMonitor

    bridge = bridge or MockBridge()
    config: dict = {"poll_interval": 1.0, **config_overrides}
    with patch("zelos_opal.extension.LiveBridge", return_value=bridge):
        monitor = OpalMonitor(config)
    monitor._dispatch = lambda fn, *a, **kw: fn(*a, **kw)
    monitor._dispatch_async = lambda fn, *a, **kw: fn(*a, **kw)
    return monitor


def _setup_actions(monitor):
    """Point the actions module at the given monitor."""
    from zelos_opal import actions

    actions.init(monitor)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_sanitize_name(check) -> None:
    check.that(sanitize_name("va_rms"), "==", "va_rms")
    check.that(sanitize_name("port1(1)"), "==", "port1_1")
    check.that(sanitize_name("model/sub/signal"), "==", "model_sub_signal")
    check.that(sanitize_name("Data_10_RAW"), "==", "Data_10_RAW")
    check.that(sanitize_name(""), "==", "signal")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_monitor_creates_with_mock_bridge(check) -> None:
    monitor = _make_monitor()
    check.that(monitor.running, "is false")
    check.that(monitor.source, "is instance of", object)


def test_monitor_start_stop(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    check.that(monitor.running, "is true")
    check.that(bridge.connected, "is true")
    check.that(len(monitor.signal_infos), "==", 3)

    monitor.stop()
    check.that(monitor.running, "is false")
    check.that(bridge.connected, "is false")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_finds_all_types(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    check.that(len(monitor.signal_infos), "==", 3)
    check.that(len(monitor.control_signal_infos), "==", 1)
    check.that(len(monitor.param_infos), "==", 1)
    monitor.stop()


# ---------------------------------------------------------------------------
# Monitor accessors
# ---------------------------------------------------------------------------


def test_status_when_stopped(check) -> None:
    monitor = _make_monitor()
    status = monitor.status()
    check.that(status["running"], "is false")
    check.that(status["model_state"], "==", "DISCONNECTED")


def test_status_when_running(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    monitor.model_state = bridge.get_model_state()
    status = monitor.status()
    check.that(status["running"], "is true")
    check.that(status["model_state"], "==", "RUNNING")
    check.that(status["signal_count"], "==", 3)
    check.that(status["control_signal_count"], "==", 1)
    check.that(status["parameter_count"], "==", 1)
    monitor.stop()


def test_status_reports_param_chunk_size_default(check) -> None:
    """status() surfaces the default chunk size (100) and chunk count."""
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    st = monitor.status()
    check.that(st["param_chunk_size"], "==", 100)
    check.that(st["param_chunk_count"], "==", 1)
    monitor.stop()


def test_status_reports_param_chunk_size_override(check) -> None:
    """Config override for param_chunk_size is respected and floored at 1."""
    monitor = _make_monitor(param_chunk_size=500)
    check.that(monitor.status()["param_chunk_size"], "==", 500)
    monitor_floor = _make_monitor(param_chunk_size=0)
    check.that(monitor_floor.status()["param_chunk_size"], "==", 1)


def test_read_signals(check) -> None:
    monitor = _make_monitor()
    result = monitor.read_signals(("mock/v", "mock/i"))
    check.that(result["mock/v"], "==", 120.0)
    check.that(result["mock/i"], "==", 50.0)


def test_set_signals(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.set_signals(("mock/v",), (240.0,))
    check.that(bridge._signal_values["mock/v"], "==", 240.0)
    check.that(bridge.signal_control_held, "is false")


def test_read_parameters(check) -> None:
    monitor = _make_monitor()
    result = monitor.read_parameters(("mock/blk/Gain",))
    check.that(result["mock/blk/Gain"], "==", 1.0)


def test_set_parameters(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.set_parameters(("mock/blk/Gain",), (2.5,))
    check.that(bridge._param_values["mock/blk/Gain"], "==", 2.5)
    check.that(bridge.param_control_held, "is false")


def test_set_parameters_logs_readback_to_trace(check) -> None:
    """set_parameters logs read-back values onto the same event as bulk polls.

    The test harness runs the async read-back inline (dispatch_async is
    patched to direct-call), so by the time set_parameters returns the
    trace log has already fired.  In production the read-back runs later
    on the main thread — see ``test_dispatch_async_enqueues_without_blocking``.
    """
    from unittest.mock import MagicMock

    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    monitor.source.log = MagicMock()

    monitor.set_parameters(("mock/blk/Gain",), (2.5,))

    check.that(bridge._param_values["mock/blk/Gain"], "==", 2.5)
    check.that(monitor.source.log.call_count, "==", 1)
    evt, data = monitor.source.log.call_args.args
    check.that(evt, "==", "mock/blk")
    check.that(data, "==", {"Gain": 2.5})
    monitor.stop()


def test_set_parameters_skips_log_for_unknown_path(check) -> None:
    """Unknown (never-discovered) param paths don't crash the read-back log."""
    from unittest.mock import MagicMock

    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    monitor.source.log = MagicMock()

    monitor.set_parameters(("mock/nonexistent/Gain",), (9.0,))

    check.that(monitor.source.log.call_count, "==", 0)
    monitor.stop()


def test_set_signals_logs_readback_to_trace(check) -> None:
    """set_signals reads the control signal back (by id) and logs it to trace.

    This confirms the write landed end-to-end without waiting for the next
    signal poll — and works even for CONTROL signals which are not part of
    the bulk signal-read groups.
    """
    from unittest.mock import MagicMock

    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    monitor.source.log = MagicMock()

    monitor.set_signals(("mock/ctrl",), (7.0,))

    check.that(bridge._signal_values["mock/ctrl"], "==", 7.0)
    check.that(monitor.source.log.call_count, "==", 1)
    evt, data = monitor.source.log.call_args.args
    check.that(evt, "==", "mock")
    check.that(data, "==", {"ctrl": 7.0})
    monitor.stop()


def test_dispatch_async_enqueues_without_blocking(check) -> None:
    """_dispatch_async returns immediately and executes on next _drain_commands.

    Validates the real async primitive (not the test-harness shortcut).  The
    set-readback path relies on this to decouple action latency from the
    GET round-trip that follows the SET.
    """
    from unittest.mock import patch

    from zelos_opal.extension import OpalMonitor

    bridge = MockBridge()
    with patch("zelos_opal.extension.LiveBridge", return_value=bridge):
        monitor = OpalMonitor({"poll_interval": 1.0})

    ran: list[int] = []

    def _side_effect() -> None:
        ran.append(1)

    monitor._dispatch_async(_side_effect)

    check.that(ran, "==", [])
    check.that(monitor._cmd_queue.qsize(), "==", 1)

    monitor._drain_commands()

    check.that(ran, "==", [1])
    check.that(monitor._cmd_queue.qsize(), "==", 0)


def test_dispatch_async_swallows_exceptions(check) -> None:
    """Errors in async work are logged, not raised — queue stays drainable."""
    from unittest.mock import patch

    from zelos_opal.extension import OpalMonitor

    bridge = MockBridge()
    with patch("zelos_opal.extension.LiveBridge", return_value=bridge):
        monitor = OpalMonitor({"poll_interval": 1.0})

    def _boom() -> None:
        raise RuntimeError("boom")

    monitor._dispatch_async(_boom)
    monitor._drain_commands()
    check.that(monitor._cmd_queue.qsize(), "==", 0)


# ---------------------------------------------------------------------------
# Actions — utility actions
# ---------------------------------------------------------------------------


def test_action_get_status(check) -> None:
    monitor = _make_monitor()
    _setup_actions(monitor)
    from zelos_opal.actions import get_status

    status = get_status()
    check.that(status["running"], "is false")
    check.that(status["model_state"], "==", "DISCONNECTED")


def test_action_set_poll_interval(check) -> None:
    monitor = _make_monitor()
    _setup_actions(monitor)
    from zelos_opal.actions import set_poll_interval

    result = set_poll_interval(0.5)
    check.that(result["poll_interval"], "==", 0.5)
    check.that(monitor.config["poll_interval"], "==", 0.5)


def test_action_list_signals(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    _setup_actions(monitor)
    from zelos_opal.actions import list_signals

    result = list_signals()
    check.that(result["count"], "==", 3)
    check.that(result["signals"][0]["name"], "==", "voltage")
    monitor.stop()


# ---------------------------------------------------------------------------
# Actions — factory-generated dynamic actions
# ---------------------------------------------------------------------------


def test_factory_read_signal(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    _setup_actions(monitor)
    from zelos_opal.actions import _make_read_signal

    fn = _make_read_signal("mock/v")
    result = fn()
    check.that(result["mock/v"], "==", 120.0)


def test_factory_set_signal(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    _setup_actions(monitor)
    from zelos_opal.actions import _make_set_signal

    fn = _make_set_signal("mock/ctrl")
    result = fn(value="5.0")
    check.that(result["message"], "==", "Set mock/ctrl = 5.0")
    check.that(bridge._signal_values["mock/ctrl"], "==", 5.0)


def test_factory_read_parameter(check) -> None:
    monitor = _make_monitor()
    _setup_actions(monitor)
    from zelos_opal.actions import _make_read_parameter

    fn = _make_read_parameter("mock/blk/Gain")
    result = fn()
    check.that(result["mock/blk/Gain"], "==", 1.0)


def test_factory_set_parameter(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    _setup_actions(monitor)
    from zelos_opal.actions import _make_set_parameter

    fn = _make_set_parameter("mock/blk/Gain")
    fn(value="2.5")
    check.that(bridge._param_values["mock/blk/Gain"], "==", 2.5)


def test_register_generates_correct_action_counts(check) -> None:
    """register() generates the right number of dynamic actions."""
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    _setup_actions(monitor)
    from zelos_opal import actions

    # MockBridge: 3 signals (2 DYNAMIC + 1 CONTROL), 1 control, 1 param, 0 variables
    # Expected: 3 read/signal, 1 set/signal, 1 read/param, 1 set/param
    read_sigs = [actions._make_read_signal(s.path) for s in monitor.signal_infos]
    set_sigs = [actions._make_set_signal(s.path) for s in monitor.control_signal_infos]
    read_params = [actions._make_read_parameter(f"{p.path}/{p.name}") for p in monitor.param_infos]
    set_params = [actions._make_set_parameter(f"{p.path}/{p.name}") for p in monitor.param_infos]

    check.that(len(read_sigs), "==", 3)
    check.that(len(set_sigs), "==", 1)
    check.that(len(read_params), "==", 1)
    check.that(len(set_params), "==", 1)
    monitor.stop()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_stop_tolerates_disconnect_error(check) -> None:
    class FailingBridge(MockBridge):
        def disconnect(self) -> None:
            raise RuntimeError("connection lost")

    monitor = _make_monitor(FailingBridge())
    monitor.running = True
    monitor.stop()
    check.that(monitor.running, "is false")


# ---------------------------------------------------------------------------
# Hierarchical signal path integration tests
# ---------------------------------------------------------------------------

_HIER_PATHS = [
    "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_RAW/port1",
    "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_SWITCH/port1",
    "Model/Sub/CAN_Main/CH00_Main/Data_2/Out1/port1",
]

_HIER_PARAM_PATHS = [
    "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_GAIN",
    "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_BIAS",
]


class HierarchicalMockBridge(MockBridge):
    """MockBridge with realistic hierarchical OPAL-RT signal and param paths."""

    SIGNALS = [
        SignalInfo(name="signal1", path=_HIER_PATHS[0], signal_id=10),
        SignalInfo(name="signal1", path=_HIER_PATHS[1], signal_id=11),
        SignalInfo(name="signal1", path=_HIER_PATHS[2], signal_id=12),
    ]
    PARAMS = [
        ParameterInfo(name="Gain", path=_HIER_PARAM_PATHS[0], variable="", value=1.0),
        ParameterInfo(name="Value", path=_HIER_PARAM_PATHS[1], variable="", value=0.0),
    ]

    _HIER_VALUES = {p: float(i + 1) for i, p in enumerate(_HIER_PATHS)}
    _PARAM_VALUES = {
        f"{_HIER_PARAM_PATHS[0]}/Gain": 1.0,
        f"{_HIER_PARAM_PATHS[1]}/Value": 0.0,
    }

    def get_signals_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._HIER_VALUES.get(n, 0.0) for n in names)

    def get_signals_by_id(self, signal_type: int, signal_ids: tuple[int, ...]) -> tuple[float, ...]:
        by_id = {s.signal_id: self._HIER_VALUES.get(s.path, 0.0) for s in self.SIGNALS}
        return tuple(by_id.get(sid, 0.0) for sid in signal_ids)

    def get_parameters_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._PARAM_VALUES.get(n, 0.0) for n in names)


def test_split_signal_path_hierarchy(check) -> None:
    evt, fld = _split_signal_path("Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_RAW/port1")
    check.that(evt, "==", "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_RAW")
    check.that(fld, "==", "port1")


def test_split_signal_path_preserves_case(check) -> None:
    evt, fld = _split_signal_path("Prefix/Block_A/Signal_X/port1")
    check.that(evt, "==", "Prefix/Block_A/Signal_X")
    check.that(fld, "==", "port1")


def test_split_signal_path_fallbacks(check) -> None:
    evt, fld = _split_signal_path("port1")
    check.that(evt, "==", "signals")
    check.that(fld, "==", "port1")

    evt, fld = _split_signal_path("single_name")
    check.that(evt, "==", "signals")
    check.that(fld, "==", "single_name")


def test_discover_builds_hierarchical_events(check) -> None:
    bridge = HierarchicalMockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()

    check.that(len(monitor._trace_signals), "==", 3)
    check.that(len(monitor._trace_params), "==", 2)

    sig_events = {evt for _, evt, _ in monitor._trace_signals}
    check.that("Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_RAW" in sig_events, "is true")
    check.that("Model/Sub/CAN_Main/CH00_Main/Data_2/Out1" in sig_events, "is true")

    for _, _, fld in monitor._trace_signals:
        check.that(fld, "==", "port1")

    gain_evt = "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_GAIN"
    bias_evt = "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_BIAS"
    param_map = {(evt, fld) for _, evt, fld in monitor._trace_params}
    check.that((gain_evt, "Gain") in param_map, "is true")
    check.that((bias_evt, "Value") in param_map, "is true")
    monitor.stop()


def test_discover_merges_signal_and_param_fields_on_shared_event(check) -> None:
    """Signals and params sharing an event path register as a single merged schema.

    The SDK does not support dynamic schemas — a log call with a field that
    wasn't registered on the event would fail.  This test proves that both
    a signal field and a parameter field landing on the same event path
    end up in one ``add_event`` call with all fields declared up front.
    """

    class MergeBridge(MockBridge):
        SIGNALS = [
            SignalInfo(name="out", path="Model/Block/out"),
        ]
        PARAMS = [
            ParameterInfo(name="Gain", path="Model/Block", variable="", value=1.0),
        ]

    monitor = _make_monitor(MergeBridge())
    monitor.start()

    sig = monitor._trace_signals[0]
    par = monitor._trace_params[0]
    check.that(sig[1], "==", "Model/Block")
    check.that(sig[2], "==", "out")
    check.that(par[1], "==", "Model/Block")
    check.that(par[2], "==", "Gain")

    monitor.source.log("Model/Block", {"out": 1.0, "Gain": 2.0})
    monitor.stop()


def test_discover_deduplicates_fields(check) -> None:
    """Two identical signal paths get distinct field names via dedup suffix."""
    dup_paths = [
        "M/S/Block/Out",
        "M/S/Block/Out",
    ]

    class DupBridge(MockBridge):
        SIGNALS = [
            SignalInfo(name="s", path=dup_paths[0]),
            SignalInfo(name="s", path=dup_paths[1]),
        ]

    monitor = _make_monitor(DupBridge())
    monitor.start()
    fields = [fld for _, _, fld in monitor._trace_signals]
    check.that(fields, "==", ["Out", "Out_1"])
    monitor.stop()


def test_hierarchical_source_log(check) -> None:
    """Verify source.log() works for /-separated event names end-to-end."""
    bridge = HierarchicalMockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()

    evt_name = "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_RAW"
    logged = False
    try:
        monitor.source.log(evt_name, {"port1": 1.0})
        logged = True
    except Exception as exc:
        check.that(False, "is true", f"source.log raised: {exc}")
    check.that(logged, "is true")
    monitor.stop()


# ---------------------------------------------------------------------------
# Poll-cycle integration (GetSignalsById + GetParametersByName tracing path)
# ---------------------------------------------------------------------------


def test_poll_cycle_traces_signals_and_params(check) -> None:
    """Simulate one iteration of the run() poll loop end-to-end."""
    bridge = HierarchicalMockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()

    param_names = tuple(path for path, _, _ in monitor._trace_params)
    check.that(len(monitor._trace_signals), "==", 3)
    check.that(len(param_names), "==", 2)

    by_event: dict[str, dict[str, float]] = {}

    for sig_type, ids, mapping in monitor._signal_read_groups:
        values = bridge.get_signals_by_id(sig_type.value, ids)
        for (evt, fld), val in zip(mapping, values, strict=False):
            by_event.setdefault(evt, {})[fld] = val

    pvalues = bridge.get_parameters_by_name(param_names)
    for (_, evt, fld), val in zip(monitor._trace_params, pvalues, strict=False):
        by_event.setdefault(evt, {})[fld] = val

    for evt, data in by_event.items():
        monitor.source.log(evt, data)

    raw_evt = "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_RAW"
    check.that(raw_evt in by_event, "is true")
    check.that(by_event[raw_evt]["port1"], "==", 1.0)

    gain_evt = "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_GAIN"
    check.that(gain_evt in by_event, "is true")
    check.that(by_event[gain_evt]["Gain"], "==", 1.0)

    bias_evt = "Model/Sub/CAN_Control/CH00/Data_1_TRIM/Data_1_BIAS"
    check.that(bias_evt in by_event, "is true")
    check.that(by_event[bias_evt]["Value"], "==", 0.0)
    monitor.stop()


def test_discover_groups_signals_by_type_for_by_id_reads(check) -> None:
    """Traced signals are grouped by SignalType for batched GetSignalsById."""
    from zelos_opal.constants import SignalType

    class MultiTypeBridge(MockBridge):
        SIGNALS = [
            SignalInfo(name="v", path="m/v", signal_type=SignalType.DYNAMIC, signal_id=1),
            SignalInfo(name="i", path="m/i", signal_type=SignalType.DYNAMIC, signal_id=2),
            SignalInfo(name="a", path="m/a", signal_type=SignalType.ACQUISITION, signal_id=3),
        ]

    monitor = _make_monitor(MultiTypeBridge())
    monitor.start()

    groups = {sig_type: (ids, mapping) for sig_type, ids, mapping in monitor._signal_read_groups}
    check.that(set(groups.keys()), "==", {SignalType.DYNAMIC, SignalType.ACQUISITION})
    check.that(groups[SignalType.DYNAMIC][0], "==", (1, 2))
    check.that(groups[SignalType.ACQUISITION][0], "==", (3,))
    monitor.stop()


# ---------------------------------------------------------------------------
# Parameter name format (matches RT-LAB path/name convention)
# ---------------------------------------------------------------------------


def test_discover_builds_param_chunks(check) -> None:
    """Discovery slices params into fixed-size chunks for rolling reads."""
    params = [ParameterInfo(name=f"p{i}", path="m/blk", variable="", value=0.0) for i in range(5)]

    class ChunkBridge(MockBridge):
        PARAMS = params

    monitor = _make_monitor(ChunkBridge(), param_chunk_size=2)
    monitor.start()

    check.that(len(monitor._param_chunks), "==", 3)
    check.that(len(monitor._param_chunks[0][0]), "==", 2)
    check.that(len(monitor._param_chunks[1][0]), "==", 2)
    check.that(len(monitor._param_chunks[2][0]), "==", 1)
    monitor.stop()


def test_run_loop_reads_one_chunk_per_cycle(check) -> None:
    """run() reads exactly one param chunk per cycle, rolling through all.

    Proves the rolling-chunk architecture: N cycles → N chunk reads, each
    covering ``chunk_size`` names (last chunk may be smaller).  No cycle
    ever reads all params at once, so signal cadence stays decoupled from
    full param-sweep time.
    """
    from unittest.mock import patch

    import zelos_opal.extension as ext_module

    params = [ParameterInfo(name=f"p{i}", path="m/blk", variable="", value=0.0) for i in range(10)]

    class ChunkBridge(MockBridge):
        PARAMS = params

        def get_parameters_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
            return tuple(0.0 for _ in names)

    bridge = ChunkBridge()
    monitor = _make_monitor(bridge, param_chunk_size=4)
    monitor.start()

    chunk_calls: list[int] = []
    orig = bridge.get_parameters_by_name

    def _counting(names: tuple[str, ...]) -> tuple[float, ...]:
        chunk_calls.append(len(names))
        return orig(names)

    bridge.get_parameters_by_name = _counting

    cycles = [0]

    def fake_sleep(self, seconds: float) -> None:
        cycles[0] += 1
        if cycles[0] >= 7:
            self.running = False

    with patch.object(ext_module.OpalMonitor, "_sleep_with_drain", fake_sleep, create=False):
        monitor.run()

    # 10 params / chunk_size=4 → chunks of [4, 4, 2] rolling over 7 cycles.
    check.that(chunk_calls, "==", [4, 4, 2, 4, 4, 2, 4])
    monitor.stop()


def test_sweep_summary_logged_on_wrap(check) -> None:
    """Full-sweep summary fires when chunk index wraps to 0."""
    import logging as _logging

    params = [ParameterInfo(name=f"p{i}", path="m/blk", variable="", value=0.0) for i in range(4)]

    class ChunkBridge(MockBridge):
        PARAMS = params

        def get_parameters_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
            return tuple(0.0 for _ in names)

    monitor = _make_monitor(ChunkBridge(), param_chunk_size=2)
    monitor.start()

    records: list[str] = []

    class _Capture(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = _Capture(level=_logging.INFO)
    ext_logger = _logging.getLogger("zelos_opal.extension")
    ext_logger.addHandler(handler)
    try:
        by_event: dict[str, dict[str, float]] = {}
        monitor._read_next_param_chunk(by_event)
        check.that(any("sweep complete" in r for r in records), "is false")
        monitor._read_next_param_chunk(by_event)
        check.that(any("sweep complete" in r for r in records), "is true")
    finally:
        ext_logger.removeHandler(handler)
    monitor.stop()


def test_parameter_api_path_format(check) -> None:
    """Parameter API path must be 'block_path/param_name' per RT-LAB convention."""
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()

    result = monitor.read_parameters(("mock/blk/Gain",))
    check.that(result["mock/blk/Gain"], "==", 1.0)

    monitor.set_parameters(("mock/blk/Gain",), (3.14,))
    check.that(bridge._param_values["mock/blk/Gain"], "==", 3.14)
    check.that(bridge.param_control_held, "is false")
    monitor.stop()
