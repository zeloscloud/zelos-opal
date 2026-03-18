"""OPAL-RT signal monitoring implementation."""

import logging
import math
import random
import time
from typing import Any

import zelos_sdk

logger = logging.getLogger(__name__)


class OpalMonitor:
    """Monitors simulated OPAL-RT signals and streams to Zelos.

    This is a scaffolding implementation that generates synthetic power-system
    signals. When custom OPAL-RT packages are integrated, the data source
    will be replaced with real target acquisition.
    """

    STATUS = {
        0: "OK",
        1: "WARNING",
        2: "FAULT",
    }

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.running = False
        self._start_time = time.time()

        self.source = zelos_sdk.TraceSourceCacheLast("opal")
        self._define_schema()

    def start(self) -> None:
        """Start monitoring."""
        logger.info("Starting OPAL-RT monitor")
        self.running = True
        self._start_time = time.time()

    def stop(self) -> None:
        """Stop monitoring."""
        logger.info("Stopping OPAL-RT monitor")
        self.running = False

    def run(self) -> None:
        """Main monitoring loop generating synthetic power-system signals."""
        loop_count = 0
        while self.running:
            t = time.time() - self._start_time

            # 3-phase voltage RMS (nominal 120V, slight drift)
            va = 120.0 * (1.0 + 0.02 * math.sin(t * 0.1))
            vb = 120.0 * (1.0 + 0.02 * math.sin(t * 0.1 + 2.094))
            vc = 120.0 * (1.0 + 0.02 * math.sin(t * 0.1 + 4.189))

            # 3-phase current (varying load)
            load = 50.0 * (1.0 + 0.3 * math.sin(t * 0.05))
            ia = max(0.0, load + random.gauss(0, 1.0))
            ib = max(0.0, load + random.gauss(0, 1.0))
            ic = max(0.0, load + random.gauss(0, 1.0))

            # Derived signals
            frequency = 60.0 + 0.05 * math.sin(t * 0.3)
            active_power = (va * ia + vb * ib + vc * ic) * 0.85
            reactive_power = active_power * 0.62  # ~PF 0.85

            # Determine status
            status = 0
            last_power = self.source.analog_outputs.active_power.get()
            if last_power is not None:
                if last_power > 25000:
                    status = 2  # FAULT
                elif last_power > 20000:
                    status = 1  # WARNING

            self.source.analog_outputs.log(
                va_rms=va,
                vb_rms=vb,
                vc_rms=vc,
                ia_rms=ia,
                ib_rms=ib,
                ic_rms=ic,
                frequency=frequency,
                active_power=active_power,
                reactive_power=reactive_power,
                status=status,
            )

            loop_count += 1
            if loop_count % 10 == 0:
                logger.info(
                    "Va=%.1fV Ia=%.1fA P=%.0fW f=%.2fHz status=%s",
                    va,
                    ia,
                    active_power,
                    frequency,
                    self.STATUS[status],
                )

            time.sleep(self.config.get("poll_interval", 1.0))

    @zelos_sdk.action("Set Poll Interval", "Change data acquisition rate")
    @zelos_sdk.action.number(
        "seconds",
        minimum=0.01,
        maximum=60.0,
        default=1.0,
        title="Interval (seconds)",
        description="Data acquisition interval",
        widget="range",
    )
    def set_poll_interval(self, seconds: float) -> dict[str, Any]:
        """Update the poll interval."""
        self.config["poll_interval"] = seconds
        return {"message": f"Poll interval set to {seconds}s", "poll_interval": seconds}

    @zelos_sdk.action("Get Status", "Get current monitoring status")
    def get_status(self) -> dict[str, Any]:
        """Get current status and latest signal values."""
        return {
            "running": self.running,
            "poll_interval": self.config.get("poll_interval", 1.0),
            "va_rms": self.source.analog_outputs.va_rms.get(),
            "ia_rms": self.source.analog_outputs.ia_rms.get(),
            "active_power": self.source.analog_outputs.active_power.get(),
            "frequency": self.source.analog_outputs.frequency.get(),
            "status": self.STATUS.get(self.source.analog_outputs.status.get() or 0, "UNKNOWN"),
        }

    def _define_schema(self) -> None:
        """Define trace schema for OPAL-RT signals."""
        self.source.add_event(
            "analog_outputs",
            [
                zelos_sdk.TraceEventFieldMetadata("va_rms", zelos_sdk.DataType.Float32, "V"),
                zelos_sdk.TraceEventFieldMetadata("vb_rms", zelos_sdk.DataType.Float32, "V"),
                zelos_sdk.TraceEventFieldMetadata("vc_rms", zelos_sdk.DataType.Float32, "V"),
                zelos_sdk.TraceEventFieldMetadata("ia_rms", zelos_sdk.DataType.Float32, "A"),
                zelos_sdk.TraceEventFieldMetadata("ib_rms", zelos_sdk.DataType.Float32, "A"),
                zelos_sdk.TraceEventFieldMetadata("ic_rms", zelos_sdk.DataType.Float32, "A"),
                zelos_sdk.TraceEventFieldMetadata("frequency", zelos_sdk.DataType.Float32, "Hz"),
                zelos_sdk.TraceEventFieldMetadata("active_power", zelos_sdk.DataType.Float32, "W"),
                zelos_sdk.TraceEventFieldMetadata(
                    "reactive_power", zelos_sdk.DataType.Float32, "VAR"
                ),
                zelos_sdk.TraceEventFieldMetadata("status", zelos_sdk.DataType.UInt8),
            ],
        )

        self.source.add_value_table("analog_outputs", "status", self.STATUS)
