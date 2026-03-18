#!/usr/bin/env python3
"""Zelos extension for OPAL-RT real-time simulation systems."""

import logging
import signal
from types import FrameType

import zelos_sdk
from zelos_sdk.extensions import load_config
from zelos_sdk.hooks.logging import TraceLoggingHandler

from zelos_opal.extension import OpalMonitor

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    config = load_config()
    monitor = OpalMonitor(config)

    # Register actions BEFORE init
    zelos_sdk.actions_registry.register(monitor)

    # Init SDK — advertises actions to the agent
    zelos_sdk.init(name="zelos_opal", actions=True)

    handler = TraceLoggingHandler("zelos_opal_logger")
    logging.getLogger().addHandler(handler)

    def shutdown_handler(signum: int, frame: FrameType | None) -> None:
        """Handle graceful shutdown on SIGTERM or SIGINT."""
        logger.info("Shutting down...")
        monitor.stop()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    logger.info("Starting zelos-opal")
    monitor.start()
    monitor.run()
