"""Fake PowerIQ client that returns realistic simulated data.

Mimics the real PowerIQ v9.x API structure where readings are embedded
inline in each resource object and pagination uses limit/offset.
"""

from __future__ import annotations

import random


class FakePowerIQClient:
    """Drop-in replacement for PowerIQClient that generates fake metrics."""

    def get_data_centers(self) -> list[dict]:
        return [
            {"id": 1, "name": "DC-East", "parent": {"type": None, "id": None}},
            {"id": 2, "name": "DC-West", "parent": {"type": None, "id": None}},
        ]

    def get_racks(self) -> list[dict]:
        return [
            {"id": 1, "name": "Rack-A01", "parent": {"type": "data_center", "id": 1}},
            {"id": 2, "name": "Rack-A02", "parent": {"type": "data_center", "id": 1}},
            {"id": 3, "name": "Rack-B01", "parent": {"type": "data_center", "id": 2}},
        ]

    def get_pdus(self) -> list[dict]:
        pdus = [
            {"id": 1, "name": "PDU-A01-A", "parent": {"type": "rack", "id": 1}},
            {"id": 2, "name": "PDU-A01-B", "parent": {"type": "rack", "id": 1}},
            {"id": 3, "name": "PDU-A02-A", "parent": {"type": "rack", "id": 2}},
            {"id": 4, "name": "PDU-B01-A", "parent": {"type": "rack", "id": 3}},
        ]
        for pdu in pdus:
            pdu["reading"] = {
                "inlet_readings": [{
                    "active_power": str(round(3500 + random.uniform(-500, 1000), 1)),
                    "apparent_power": str(round(3800 + random.uniform(-500, 1000), 1)),
                    "current": round(15.0 + random.uniform(-3, 5), 1),
                    "voltage": round(230.0 + random.uniform(-5, 5), 1),
                }],
            }
        return pdus

    def get_outlets(self) -> list[dict]:
        outlets = []
        states = ["ON", "ON", "OFF", "ON", "ON"]
        for i in range(5):
            outlets.append({
                "id": 10 + i,
                "pdu_id": 1,
                "ordinal": i + 1,
                "name": f"OUTLET {i + 1}",
                "outlet_name": f"OUTLET {i + 1}",
                "state": states[i],
                "reading": {
                    "active_power": str(round(150 + random.uniform(-50, 100), 1)),
                    "current": round(0.7 + random.uniform(-0.2, 0.5), 2),
                },
            })
        return outlets

    def get_sensors(self) -> list[dict]:
        return [
            {
                "id": 100, "name": "T1", "pdu_id": 1,
                "type": "TemperatureSensor",
                "parent": {"type": "rack", "id": 1},
                "reading": {"value": round(22.0 + random.uniform(-2, 5), 1), "uom": "deg C"},
                "state": {},
            },
            {
                "id": 101, "name": "T2", "pdu_id": 2,
                "type": "TemperatureSensor",
                "parent": {"type": "rack", "id": 1},
                "reading": {"value": round(24.0 + random.uniform(-2, 5), 1), "uom": "deg C"},
                "state": {},
            },
            {
                "id": 102, "name": "RH", "pdu_id": 3,
                "type": "HumiditySensor",
                "parent": {"type": "rack", "id": 2},
                "reading": {"value": round(45.0 + random.uniform(-10, 15), 1), "uom": "%"},
                "state": {},
            },
            {
                "id": 103, "name": "Door-Rack-A01", "pdu_id": 1,
                "type": "ContactClosureSensor",
                "parent": {"type": "rack", "id": 1},
                "reading": {},
                "state": {},
            },
        ]

    def get_circuit_breakers(self) -> list[dict]:
        return [
            {"id": 200, "name": "B1", "pdu_id": 1, "state": 3},
            {"id": 201, "name": "B2", "pdu_id": 1, "state": 3},
            {"id": 202, "name": "B1", "pdu_id": 3, "state": 3},
        ]

    def get_events(self) -> list[dict]:
        return [
            {"severity": "WARNING", "name": "High temperature", "cleared_at": None},
            {"severity": "WARNING", "name": "Humidity out of range", "cleared_at": None},
            {"severity": "CRITICAL", "name": "Data collection failed", "cleared_at": None},
            {"severity": "INFORMATIONAL", "name": "PDU discovered", "cleared_at": "2026/01/01 00:00:00 +0000"},
        ]
