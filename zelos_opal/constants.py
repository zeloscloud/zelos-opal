"""RT-LAB enumerations and data structures.

Mirrors constants from OpalApi.h.
See https://opal-rt.atlassian.net/wiki/spaces/PRD/pages/144150199
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ModelState(enum.IntEnum):
    """OP_MODEL_STATE — RT-LAB model lifecycle states."""

    NOT_LOADABLE = 1
    COMPILING = 2
    LOADABLE = 3
    LOADING = 4
    LOADED = 5
    PAUSED = 6
    RUNNING = 7
    RESETTING = 8
    DISCONNECTED = 9
    NOT_CONNECTED = 10


class SignalType(enum.IntEnum):
    """OP_SIGNAL_TYPE — RT-LAB signal categories."""

    ACQUISITION = 0
    DYNAMIC = 1
    CONTROL = 2

    @classmethod
    def from_raw(cls, value: Any) -> SignalType:
        """Coerce an RT-LAB SWIG enum / int to a ``SignalType``."""
        try:
            return cls(int(value))
        except (ValueError, TypeError):
            return cls.DYNAMIC


class ControlOp(enum.IntEnum):
    """Argument to GetSignalControl / GetParameterControl."""

    RELEASE = 0
    ACQUIRE = 1


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SignalInfo:
    """Metadata for one RT-LAB signal (acquisition, dynamic, or control)."""

    name: str
    path: str = ""
    label: str = ""
    signal_type: SignalType = SignalType.DYNAMIC
    subsystem_id: int = 0
    num_elements: int = 1


@dataclass
class ParameterInfo:
    """Metadata for one RT-LAB block parameter."""

    name: str
    path: str = ""
    variable: str = ""
    param_id: int = 0
    value: float = 0.0


@dataclass
class VariableInfo:
    """Metadata for one RT-LAB workspace variable."""

    name: str
    value: float = 0.0


@dataclass
class AcquisitionFrame:
    """One sample returned from an acquisition group."""

    signal_values: tuple[float, ...]
    missed_data: float = 0.0
    sim_time: float = 0.0
    sample_rate: float = 0.0
    time_step: float = 0.0
    end_frame: bool = False


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def sanitize_name(name: str) -> str:
    """Convert an RT-LAB signal/parameter name to a valid trace field name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_") or "signal"
