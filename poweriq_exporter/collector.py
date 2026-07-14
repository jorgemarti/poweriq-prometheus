"""Prometheus custom collector backed by a background-polling cache.

Readings in PowerIQ v9.x are returned inline with each resource object:
- PDUs:     ``pdu.reading.inlet_readings[0]``  (active_power, apparent_power, current, voltage — may be strings)
- Outlets:  ``outlet.reading``                  (active_power, current — may be strings)
- Sensors:  ``sensor.reading.value`` + ``sensor.type`` to classify
- Sensors:  ``sensor.state`` for two-state sensors (contact closure, etc.)
- Breakers: ``circuit_breaker.state`` (numeric)

Location hierarchy is resolved by fetching racks and data_centers once per
poll cycle and building a rack_id -> {rack_name, data_center_name} lookup.

Sensor types (per official API docs):
  TemperatureSensor, HumiditySensor, AbsoluteHumiditySensor,
  AirFlowSensor, AirPressureSensor, ContactClosureSensor,
  MotionDetectionSensor, SmokeSensor, TamperDetectionSensor,
  WaterSensor, VibrationSensor, VibrationAccelerationSensor
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Any

from prometheus_client.core import GaugeMetricFamily

from .client import PowerIQClient

logger = logging.getLogger(__name__)

SEVERITY_LEVELS = ("CRITICAL", "WARNING", "INFORMATIONAL")

# Sensor types that produce a numeric reading (value + uom).
_NUMERIC_SENSOR_MAP = {
    "TemperatureSensor":          ("poweriq_sensor_temperature_celsius", "Sensor temperature in Celsius"),
    "HumiditySensor":             ("poweriq_sensor_humidity_percent",    "Sensor relative humidity percentage"),
    "AbsoluteHumiditySensor":     ("poweriq_sensor_absolute_humidity",   "Sensor absolute humidity (g/m³)"),
    "AirFlowSensor":              ("poweriq_sensor_air_flow",            "Sensor air flow"),
    "AirPressureSensor":          ("poweriq_sensor_air_pressure",        "Sensor air pressure"),
    "VibrationSensor":            ("poweriq_sensor_vibration",           "Sensor vibration"),
    "VibrationAccelerationSensor":("poweriq_sensor_vibration_acceleration", "Sensor vibration acceleration"),
}

# Two-state / binary sensors: expose 1 (active/triggered) or 0 (normal).
_BINARY_SENSOR_TYPES = {
    "ContactClosureSensor",
    "MotionDetectionSensor",
    "SmokeSensor",
    "TamperDetectionSensor",
    "WaterSensor",
}


def _float(val: Any) -> float | None:
    """Safely convert a value that may be a string, number, or None to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class CachedData:
    """Thread-safe container for the latest poll results."""

    def __init__(self) -> None:
        self.pdus: list[dict] = []
        self.outlets: list[dict] = []
        self.sensors: list[dict] = []
        self.circuit_breakers: list[dict] = []
        self.active_events: list[dict] = []
        self.rack_map: dict[int, dict] = {}        # rack_id -> {name, data_center}
        self.scrape_duration: float = 0.0
        self.scrape_success: bool = False
        self.api_request_counts: Counter = Counter()
        self.lock = threading.Lock()


class PowerIQCollector:
    """Prometheus collector that serves cached PowerIQ data."""

    def __init__(self, client: PowerIQClient, scrape_interval: int = 60) -> None:
        self._client = client
        self._scrape_interval = scrape_interval
        self._cache = CachedData()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=10)

    # ------------------------------------------------------------------
    # Background polling
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            self._stop_event.wait(self._scrape_interval)

    def _poll_once(self) -> None:
        t0 = time.monotonic()
        success = True
        try:
            # Build location lookup first
            data_centers = self._safe_call("data_centers", self._client.get_data_centers)
            dc_map = {dc["id"]: dc.get("name", "") for dc in data_centers}

            racks = self._safe_call("racks", self._client.get_racks)
            rack_map: dict[int, dict] = {}
            for rack in racks:
                rack_id = rack.get("id")
                parent = rack.get("parent", {})
                dc_id = parent.get("id")
                rack_map[rack_id] = {
                    "name": rack.get("name", ""),
                    "data_center": dc_map.get(dc_id, ""),
                }

            pdus = self._safe_call("pdus", self._client.get_pdus)
            outlets = self._safe_call("outlets", self._client.get_outlets)
            sensors = self._safe_call("sensors", self._client.get_sensors)
            breakers = self._safe_call("circuit_breakers", self._client.get_circuit_breakers)
            events = self._safe_call("events", self._client.get_events)

            # Filter to active (uncleared) events
            active_events = [e for e in events if not e.get("cleared_at")]

            with self._cache.lock:
                self._cache.rack_map = rack_map
                self._cache.pdus = pdus
                self._cache.outlets = outlets
                self._cache.sensors = sensors
                self._cache.circuit_breakers = breakers
                self._cache.active_events = active_events

        except Exception as exc:
            logger.error("Poll cycle failed — %s", exc)
            logger.debug("Poll cycle failed", exc_info=True)
            success = False

        elapsed = time.monotonic() - t0
        with self._cache.lock:
            self._cache.scrape_duration = elapsed
            self._cache.scrape_success = success
        logger.info("Poll completed in %.2fs (success=%s)", elapsed, success)

    def _safe_call(self, label: str, fn, *args) -> Any:
        try:
            result = fn(*args)
            with self._cache.lock:
                self._cache.api_request_counts[(label, "ok")] += 1
            return result
        except Exception as exc:
            # One-liner at ERROR; full traceback only at DEBUG
            logger.error("API call failed: %s — %s", label, exc)
            logger.debug("API call failed: %s", label, exc_info=True)
            with self._cache.lock:
                self._cache.api_request_counts[(label, "error")] += 1
            return []

    # ------------------------------------------------------------------
    # Prometheus Collector interface
    # ------------------------------------------------------------------

    def describe(self):
        return []

    def collect(self):
        with self._cache.lock:
            pdus = list(self._cache.pdus)
            outlets = list(self._cache.outlets)
            sensors = list(self._cache.sensors)
            breakers = list(self._cache.circuit_breakers)
            active_events = list(self._cache.active_events)
            rack_map = dict(self._cache.rack_map)
            scrape_duration = self._cache.scrape_duration
            scrape_success = self._cache.scrape_success

        yield from self._collect_pdus(pdus, rack_map)
        yield from self._collect_outlets(outlets)
        yield from self._collect_sensors(sensors, rack_map)
        yield from self._collect_breakers(breakers)
        yield from self._collect_events(active_events)

        g = GaugeMetricFamily("poweriq_scrape_duration_seconds", "Time spent polling PowerIQ")
        g.add_metric([], scrape_duration)
        yield g

        g = GaugeMetricFamily("poweriq_scrape_success", "Whether the last poll succeeded (1=yes, 0=no)")
        g.add_metric([], 1.0 if scrape_success else 0.0)
        yield g

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_rack(self, obj: dict, rack_map: dict) -> tuple[str, str]:
        """Return (rack_name, data_center_name) from an object's parent rack."""
        parent = obj.get("parent", {})
        if parent.get("type") == "rack":
            info = rack_map.get(parent.get("id"), {})
            return info.get("name", ""), info.get("data_center", "")
        return "", ""

    # ------------------------------------------------------------------
    # Per-resource collectors
    # ------------------------------------------------------------------

    def _collect_pdus(self, pdus, rack_map):
        labels = ["pdu_name", "data_center", "rack"]

        active_power = GaugeMetricFamily(
            "poweriq_pdu_active_power_watts", "PDU active power in watts", labels=labels,
        )
        apparent_power = GaugeMetricFamily(
            "poweriq_pdu_apparent_power_va", "PDU apparent power in VA", labels=labels,
        )
        current = GaugeMetricFamily(
            "poweriq_pdu_current_amps", "PDU current in amps", labels=labels,
        )
        voltage = GaugeMetricFamily(
            "poweriq_pdu_voltage_volts", "PDU voltage in volts", labels=labels,
        )

        for pdu in pdus:
            name = pdu.get("name", "unknown")
            rack_name, dc_name = self._resolve_rack(pdu, rack_map)
            lv = [name, dc_name, rack_name]

            # Readings are in pdu.reading.inlet_readings[0]
            reading = pdu.get("reading", {})
            inlet_readings = reading.get("inlet_readings", [])
            if not inlet_readings:
                continue
            inlet = inlet_readings[0]

            val = _float(inlet.get("active_power"))
            if val is not None:
                active_power.add_metric(lv, val)
            val = _float(inlet.get("apparent_power"))
            if val is not None:
                apparent_power.add_metric(lv, val)
            val = _float(inlet.get("current"))
            if val is not None:
                current.add_metric(lv, val)
            val = _float(inlet.get("voltage"))
            if val is not None:
                voltage.add_metric(lv, val)

        yield active_power
        yield apparent_power
        yield current
        yield voltage

    def _collect_outlets(self, outlets):
        state_fam = GaugeMetricFamily(
            "poweriq_outlet_state", "Outlet state (0=off, 1=on)",
            labels=["pdu_id", "outlet_ordinal", "outlet_name"],
        )
        power_fam = GaugeMetricFamily(
            "poweriq_outlet_active_power_watts", "Outlet active power in watts",
            labels=["pdu_id", "outlet_ordinal", "outlet_name"],
        )
        current_fam = GaugeMetricFamily(
            "poweriq_outlet_current_amps", "Outlet current in amps",
            labels=["pdu_id", "outlet_ordinal", "outlet_name"],
        )

        for outlet in outlets:
            pdu_id = str(outlet.get("pdu_id", ""))
            ordinal = str(outlet.get("ordinal", ""))
            o_name = outlet.get("outlet_name", outlet.get("name", ""))
            lv = [pdu_id, ordinal, o_name]

            state = (outlet.get("state") or "").upper()
            if state == "ON":
                state_fam.add_metric(lv, 1)
            elif state == "OFF":
                state_fam.add_metric(lv, 0)

            reading = outlet.get("reading", {})
            val = _float(reading.get("active_power"))
            if val is not None:
                power_fam.add_metric(lv, val)
            val = _float(reading.get("current"))
            if val is not None:
                current_fam.add_metric(lv, val)

        yield state_fam
        yield power_fam
        yield current_fam

    def _collect_sensors(self, sensors, rack_map):
        labels = ["sensor_id", "sensor_name", "sensor_type", "position",
                  "vertical_position", "pdu_id", "data_center", "rack"]

        # Build metric families for numeric sensors
        numeric_families: dict[str, GaugeMetricFamily] = {}
        for sensor_type, (metric_name, help_text) in _NUMERIC_SENSOR_MAP.items():
            numeric_families[sensor_type] = GaugeMetricFamily(
                metric_name, help_text, labels=labels,
            )

        # Binary sensors (contact closure, smoke, motion, tamper, water)
        binary_fam = GaugeMetricFamily(
            "poweriq_sensor_state",
            "Binary sensor state (1=triggered/active, 0=normal). "
            "Applies to ContactClosure, MotionDetection, Smoke, Tamper, and Water sensors.",
            labels=labels,
        )

        for sensor in sensors:
            sensor_id = str(sensor.get("id", ""))
            name = sensor.get("name", sensor.get("label", "unknown"))
            sensor_type = sensor.get("type", "")
            position = sensor.get("position", "")
            vertical_position = sensor.get("vertical_position", "")
            pdu_id = str(sensor.get("pdu_id", ""))
            rack_name, dc_name = self._resolve_rack(sensor, rack_map)
            lv = [sensor_id, name, sensor_type, position,
                  vertical_position, pdu_id, dc_name, rack_name]

            if sensor_type in _NUMERIC_SENSOR_MAP:
                reading = sensor.get("reading") or {}
                val = _float(reading.get("value"))
                if val is not None:
                    numeric_families[sensor_type].add_metric(lv, val)

            elif sensor_type in _BINARY_SENSOR_TYPES:
                state = sensor.get("state")
                if isinstance(state, dict) and state:
                    # state is a hash — any non-empty state means triggered
                    binary_fam.add_metric(lv, 1)
                elif isinstance(state, dict):
                    binary_fam.add_metric(lv, 0)

        for fam in numeric_families.values():
            yield fam
        yield binary_fam

    def _collect_breakers(self, breakers):
        state_fam = GaugeMetricFamily(
            "poweriq_circuit_breaker_state", "Circuit breaker state",
            labels=["breaker_name", "pdu_id"],
        )

        for brk in breakers:
            name = brk.get("name", "unknown")
            pdu_id = str(brk.get("pdu_id", ""))
            state = brk.get("state")
            if state is not None:
                state_fam.add_metric([name, pdu_id], float(state))

        yield state_fam

    def _collect_events(self, active_events):
        # Summary count by severity
        counts: dict[str, int] = {s: 0 for s in SEVERITY_LEVELS}
        for event in active_events:
            sev = (event.get("severity") or "INFORMATIONAL").upper()
            counts[sev] = counts.get(sev, 0) + 1

        g = GaugeMetricFamily(
            "poweriq_active_events", "Number of active (uncleared) events by severity",
            labels=["severity"],
        )
        for sev in SEVERITY_LEVELS:
            g.add_metric([sev], counts.get(sev, 0))
        yield g

        # Individual active events with detail labels.
        # Cardinality is bounded by the number of *currently active* events,
        # which is typically small.  Events disappear once cleared.
        event_fam = GaugeMetricFamily(
            "poweriq_event_active",
            "Active (uncleared) event.  Value is always 1; labels carry detail.",
            labels=["severity", "name", "eventable_type", "data_center", "rack"],
        )
        for event in active_events:
            sev = (event.get("severity") or "INFORMATIONAL").upper()
            name = event.get("name", "unknown")
            etype = event.get("eventable_type", "")
            dc = event.get("data_center_name", "")
            rack = event.get("rack_name", "")
            event_fam.add_metric([sev, name, etype, dc, rack], 1)
        yield event_fam
