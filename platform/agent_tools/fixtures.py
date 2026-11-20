"""Deterministic data used by the Agent Engine capability-analysis POC."""
from __future__ import annotations


X200_CAPABILITY_INPUT = {
    "uavId": "X200",
    "fixtureMode": True,
    "requirements": {"enduranceHours": 4.0, "payloadKg": 18.0},
    "uavData": {
        "uavId": "X200",
        "model": "X200",
        "emptyWeightKg": 82.0,
        "mtowKg": 120.0,
        "payloadKg": 20.0,
        "enduranceHours": 4.7,
        "cruiseSpeedMps": 28.0,
        "batteryCapacityWh": 1800.0,
        "batterySoh": 0.91,
    },
    "flightLogs": [
        {"energyWh": 320.0, "durationHours": 1.1},
        {"energyWh": 280.0, "durationHours": 0.9},
    ],
    "missions": [{"missionId": "M-1", "status": "completed"}],
    "batteryHistory": {"soh": 0.91, "cycleCount": 124},
}
