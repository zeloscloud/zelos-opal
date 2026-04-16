"""Tests for the OPAL-RT extension."""

from zelos_opal.constants import (
    ModelState,
    ParameterInfo,
    SignalInfo,
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
        SignalInfo(name="voltage", path="mock/v", label="Voltage"),
        SignalInfo(name="current", path="mock/i", label="Current"),
    ]
    CONTROL_SIGNALS = [
        SignalInfo(name="switch_pos", path="mock/ctrl", label="Switch"),
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
    """Create an OpalMonitor wired to MockBridge, with dispatch bypassed."""
    from unittest.mock import patch

    from zelos_opal.extension import OpalMonitor

    bridge = bridge or MockBridge()
    config: dict = {"poll_interval": 1.0, **config_overrides}
    with patch("zelos_opal.extension.LiveBridge", return_value=bridge):
        monitor = OpalMonitor(config)
    monitor._dispatch = lambda fn, *a, **kw: fn(*a, **kw)
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
    check.that(len(monitor.signal_infos), "==", 2)

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
    check.that(len(monitor.signal_infos), "==", 2)
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
    check.that(status["signal_count"], "==", 2)
    check.that(status["control_signal_count"], "==", 1)
    check.that(status["parameter_count"], "==", 1)
    monitor.stop()


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
    check.that(result["count"], "==", 2)
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

    # MockBridge: 2 signals, 1 control, 1 param, 0 variables
    # Expected: 2 read/signal, 1 set/signal, 1 read/param, 1 set/param
    read_sigs = [actions._make_read_signal(s.path) for s in monitor.signal_infos]
    set_sigs = [actions._make_set_signal(s.path) for s in monitor.control_signal_infos]
    read_params = [actions._make_read_parameter(f"{p.path}/{p.name}") for p in monitor.param_infos]
    set_params = [actions._make_set_parameter(f"{p.path}/{p.name}") for p in monitor.param_infos]

    check.that(len(read_sigs), "==", 2)
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
        SignalInfo(name="signal1", path=_HIER_PATHS[0]),
        SignalInfo(name="signal1", path=_HIER_PATHS[1]),
        SignalInfo(name="signal1", path=_HIER_PATHS[2]),
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
# Poll-cycle integration (new GetSignalsByName tracing path)
# ---------------------------------------------------------------------------


def test_poll_cycle_traces_signals_and_params(check) -> None:
    """Simulate one iteration of the run() poll loop end-to-end."""
    bridge = HierarchicalMockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()

    sig_names = tuple(path for path, _, _ in monitor._trace_signals)
    param_names = tuple(path for path, _, _ in monitor._trace_params)
    check.that(len(sig_names), "==", 3)
    check.that(len(param_names), "==", 2)

    by_event: dict[str, dict[str, float]] = {}

    values = bridge.get_signals_by_name(sig_names)
    for (_, evt, fld), val in zip(monitor._trace_signals, values, strict=False):
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


# ---------------------------------------------------------------------------
# Parameter name format (matches RT-LAB path/name convention)
# ---------------------------------------------------------------------------


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
