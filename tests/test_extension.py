"""Tests for the OPAL-RT extension."""


def test_opal_monitor_creates(check) -> None:
    """OpalMonitor can be instantiated with default config."""
    from zelos_opal.extension import OpalMonitor

    monitor = OpalMonitor({"poll_interval": 1.0})
    check.that(monitor.running, "is false")
    check.that(monitor.source, "is instance of", object)


def test_opal_monitor_start_stop(check) -> None:
    """OpalMonitor can start and stop."""
    from zelos_opal.extension import OpalMonitor

    monitor = OpalMonitor({"poll_interval": 1.0})
    monitor.start()
    check.that(monitor.running, "is true")
    monitor.stop()
    check.that(monitor.running, "is false")


def test_set_poll_interval(check) -> None:
    """set_poll_interval action updates config."""
    from zelos_opal.extension import OpalMonitor

    monitor = OpalMonitor({"poll_interval": 1.0})
    result = monitor.set_poll_interval(0.5)
    check.that(result["poll_interval"], "==", 0.5)
    check.that(monitor.config["poll_interval"], "==", 0.5)


def test_get_status(check) -> None:
    """get_status returns expected keys."""
    from zelos_opal.extension import OpalMonitor

    monitor = OpalMonitor({"poll_interval": 1.0})
    status = monitor.get_status()
    check.that(status["running"], "is false")
    check.that("poll_interval", "in", status)
    check.that("va_rms", "in", status)
    check.that("active_power", "in", status)
    check.that("frequency", "in", status)
    check.that("status", "in", status)
