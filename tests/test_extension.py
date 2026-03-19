"""Tests for the OPAL-RT extension."""

from zelos_opal.constants import (
    AcquisitionFrame,
    ModelState,
    ParameterInfo,
    SignalInfo,
    VariableInfo,
    sanitize_name,
)

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
        ParameterInfo(name="gain", path="mock/ctrl", variable="Gain", value=1.0),
    ]
    VARIABLES = [
        VariableInfo(name="K", value=10.0),
    ]

    def __init__(self) -> None:
        self.connected = False
        self.signal_control_held = False
        self.param_control_held = False
        self._signal_values: dict[str, float] = {"mock/v": 120.0, "mock/i": 50.0}
        self._param_values: dict[str, float] = {"mock/ctrl": 1.0}
        self._variable_values: dict[str, float] = {"K": 10.0}
        self._control_signal_values: tuple[float, ...] = (0.0,)

    def connect(self, project_path: str) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def get_model_state(self) -> ModelState:
        return ModelState.RUNNING if self.connected else ModelState.DISCONNECTED

    def get_signal_names_for_group(self, group: int) -> list[str]:
        return [s.name for s in self.SIGNALS] if group == 1 else []

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

    def get_control_signals(self) -> tuple[float, ...]:
        return self._control_signal_values

    def set_control_signals(self, subsystem_id: int, values: tuple[float, ...]) -> None:
        self._control_signal_values = values

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

    def get_variables_description(self) -> list[VariableInfo]:
        return list(self.VARIABLES)

    def get_variables_by_name(self, names: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(self._variable_values.get(n, 0.0) for n in names)

    def set_variables(self, names: tuple[str, ...], values: tuple[float, ...]) -> None:
        for n, v in zip(names, values, strict=True):
            self._variable_values[n] = v

    def acquire(self, acq_group: int, acq_time_step: float) -> AcquisitionFrame:
        return AcquisitionFrame(
            signal_values=(120.0, 50.0),
            sim_time=1.0,
            sample_rate=1000.0,
            time_step=acq_time_step,
            end_frame=True,
        )


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
    check.that(len(monitor.variable_infos), "==", 1)
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
    check.that(status["variable_count"], "==", 1)
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


def test_read_control_signals(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    result = monitor.read_control_signals()
    check.that(result["mock/ctrl"], "==", 0.0)
    monitor.stop()


def test_set_control_signals(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    monitor.set_control_signals(1, (5.0,))
    check.that(bridge._control_signal_values, "==", (5.0,))
    check.that(bridge.signal_control_held, "is false")
    monitor.stop()


def test_read_parameters(check) -> None:
    monitor = _make_monitor()
    result = monitor.read_parameters(("mock/ctrl",))
    check.that(result["mock/ctrl"], "==", 1.0)


def test_set_parameters(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.set_parameters(("mock/ctrl",), (2.5,))
    check.that(bridge._param_values["mock/ctrl"], "==", 2.5)
    check.that(bridge.param_control_held, "is false")


def test_read_variables(check) -> None:
    monitor = _make_monitor()
    result = monitor.read_variables(("K",))
    check.that(result["K"], "==", 10.0)


def test_set_variables(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.set_variables(("K",), (20.0,))
    check.that(bridge._variable_values["K"], "==", 20.0)


# ---------------------------------------------------------------------------
# Actions (free functions via actions module)
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


def test_action_read_signal(check) -> None:
    monitor = _make_monitor()
    _setup_actions(monitor)
    from zelos_opal.actions import read_signal

    result = read_signal("mock/v")
    check.that(result["mock/v"], "==", 120.0)


def test_action_set_signal(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    _setup_actions(monitor)
    from zelos_opal.actions import set_signal

    set_signal("mock/v", 240.0)
    check.that(bridge._signal_values["mock/v"], "==", 240.0)


def test_action_read_parameter(check) -> None:
    monitor = _make_monitor()
    _setup_actions(monitor)
    from zelos_opal.actions import read_parameter

    result = read_parameter("mock/ctrl")
    check.that(result["mock/ctrl"], "==", 1.0)


def test_action_set_parameter(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    _setup_actions(monitor)
    from zelos_opal.actions import set_parameter

    set_parameter("mock/ctrl", 2.5)
    check.that(bridge._param_values["mock/ctrl"], "==", 2.5)


def test_action_dynamic_choices(check) -> None:
    bridge = MockBridge()
    monitor = _make_monitor(bridge)
    monitor.start()
    _setup_actions(monitor)
    from zelos_opal.actions import (
        _control_signal_choices,
        _parameter_choices,
        _signal_choices,
        _variable_choices,
    )

    check.that(_signal_choices(), "==", ["mock/v", "mock/i"])
    check.that(_control_signal_choices(), "==", ["mock/ctrl"])
    check.that(_parameter_choices(), "==", ["mock/ctrl"])
    check.that(_variable_choices(), "==", ["K"])
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
