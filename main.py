#!/usr/bin/env python3
"""Zelos extension for OPAL-RT real-time simulation systems."""

import logging
import signal
from types import FrameType

import zelos_sdk
from zelos_sdk.extensions import load_config
from zelos_sdk.hooks.logging import TraceLoggingHandler

from zelos_opal import actions
from zelos_opal.extension import OpalMonitor

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    config = load_config()
    log_level = getattr(logging, config.get("log_level", "INFO"), logging.INFO)
    logging.basicConfig(level=log_level)

    monitor = OpalMonitor(config)

    actions.init(monitor)
    actions.register()

    zelos_sdk.init(name="zelos_opal", actions=True)

    handler = TraceLoggingHandler("zelos_opal_logger")
    logging.getLogger().addHandler(handler)

    def shutdown_handler(signum: int, frame: FrameType | None) -> None:
        logger.info("Shutting down...")
        monitor.stop()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    logger.info("Starting zelos-opal")
    monitor.start()
    monitor.run()
