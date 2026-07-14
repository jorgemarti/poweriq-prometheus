# PowerIQ Prometheus Exporter

A Prometheus exporter for [Sunbird PowerIQ](https://www.sunbirddcim.com/poweriq) that exposes PDU, outlet, sensor, circuit breaker, and event metrics at `/metrics`. Tested with PowerIQ v9.x (REST API v2).

## Quick start

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env` with your PowerIQ appliance address and credentials:

```bash
PIQ_HOST=poweriq.example.com
PIQ_USERNAME=monitoring
PIQ_PASSWORD=secret
TLS_INSECURE=true              # set to false once you provide a CA bundle
```

### 2. Run

**With Docker:**

```bash
docker compose up --build
```

Or without Compose:

```bash
docker build -t poweriq-exporter .
docker run -d -p 9131:9131 --env-file .env poweriq-exporter
```

**Without Docker:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' .env | grep -v '^\s*$' | xargs)
python -m poweriq_exporter.main
```

### 3. Verify

Wait ~60 seconds for the first poll, then:

```bash
# Check the exporter is up and polling successfully
curl -s http://localhost:9131/metrics | grep poweriq_scrape_success
# poweriq_scrape_success 1.0

# Check PDU metrics are being collected
curl -s http://localhost:9131/metrics | grep poweriq_pdu_active_power_watts
```

### 4. Point Prometheus at it

Add to your `prometheus.yml` (see also `examples/prometheus.yml`):

```yaml
scrape_configs:
  - job_name: poweriq
    scrape_interval: 60s
    scrape_timeout: 10s
    static_configs:
      - targets: ["localhost:9131"]
```

## Configuration

All settings are via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `PIQ_HOST` | *(required)* | PowerIQ appliance hostname |
| `PIQ_USERNAME` | *(required)* | PowerIQ username |
| `PIQ_PASSWORD` | *(required)* | PowerIQ password |
| `PIQ_API_BASE` | `/api/v2` | REST API base path |
| `PIQ_SCRAPE_INTERVAL` | `60` | Seconds between background polls |
| `EXPORTER_PORT` | `9131` | HTTP port for `/metrics` |
| `TLS_CA_BUNDLE` | | Path to a CA bundle PEM for PowerIQ HTTPS |
| `TLS_INSECURE` | `false` | Skip TLS verification (development only) |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Use `DEBUG` for full stack traces. |

## Metrics

### PDU metrics

Readings come from the first inlet of each PDU (`reading.inlet_readings[0]`).

| Metric | Labels | Description |
|--------|--------|-------------|
| `poweriq_pdu_active_power_watts` | pdu_name, data_center, rack | Active power (W) |
| `poweriq_pdu_apparent_power_va` | pdu_name, data_center, rack | Apparent power (VA) |
| `poweriq_pdu_current_amps` | pdu_name, data_center, rack | Current (A) |
| `poweriq_pdu_voltage_volts` | pdu_name, data_center, rack | Voltage (V) |

### Outlet metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `poweriq_outlet_state` | pdu_id, outlet_ordinal, outlet_name | 0=off, 1=on |
| `poweriq_outlet_active_power_watts` | pdu_id, outlet_ordinal, outlet_name | Active power (W) |
| `poweriq_outlet_current_amps` | pdu_id, outlet_ordinal, outlet_name | Current (A) |

### Sensor metrics

Sensors are classified by their `type` field. All sensor metrics include the labels: **sensor_id, sensor_name, sensor_type, position, vertical_position, pdu_id, data_center, rack**. `sensor_id` is the globally unique identifier and guarantees each physical sensor maps to its own time series even when several share the same name (e.g. multiple `RH`/`T1` sensors on one PDU); `position` (INLET/OUTLET) and `vertical_position` (TOP/MIDDLE/BOTTOM) describe the physical placement.

**Numeric sensors** (value-based readings):

| Metric | Sensor types | Description |
|--------|-------------|-------------|
| `poweriq_sensor_temperature_celsius` | TemperatureSensor | Temperature (C) |
| `poweriq_sensor_humidity_percent` | HumiditySensor | Relative humidity (%) |
| `poweriq_sensor_absolute_humidity` | AbsoluteHumiditySensor | Absolute humidity (g/m³) |
| `poweriq_sensor_air_flow` | AirFlowSensor | Air flow |
| `poweriq_sensor_air_pressure` | AirPressureSensor | Air pressure |
| `poweriq_sensor_vibration` | VibrationSensor | Vibration |
| `poweriq_sensor_vibration_acceleration` | VibrationAccelerationSensor | Vibration acceleration |

**Binary sensors** (two-state, via the `state` hash):

| Metric | Sensor types | Description |
|--------|-------------|-------------|
| `poweriq_sensor_state` | ContactClosureSensor, MotionDetectionSensor, SmokeSensor, TamperDetectionSensor, WaterSensor | 1=triggered/active, 0=normal |

### Circuit breaker metrics

| Metric | Labels | Description |
|--------|--------|-------------|
| `poweriq_circuit_breaker_state` | breaker_name, pdu_id | Breaker state (numeric) |

### Event metrics

Active events are those with no `cleared_at` timestamp.

| Metric | Labels | Description |
|--------|--------|-------------|
| `poweriq_active_events` | severity | Summary count of active events by severity (CRITICAL, WARNING, INFORMATIONAL) |
| `poweriq_event_active` | severity, name, eventable_type, data_center, rack | Individual active event (value=1). Disappears when cleared. |

### Exporter self-metrics

| Metric | Description |
|--------|-------------|
| `poweriq_scrape_duration_seconds` | Time spent polling PowerIQ |
| `poweriq_scrape_success` | 1 if the last poll succeeded, 0 otherwise |

## Dry-run mode

To test the exporter without a real PowerIQ appliance, use `--dry-run`. It serves simulated data — no credentials or connectivity needed:

```bash
python -m poweriq_exporter.main --dry-run
# or
docker run -p 9131:9131 poweriq-exporter --dry-run
```

## How it works

The exporter runs a background thread that polls the PowerIQ REST API every `PIQ_SCRAPE_INTERVAL` seconds and caches the results in memory. When Prometheus scrapes `/metrics`, it gets the cached values instantly. This avoids overloading the PowerIQ API when multiple Prometheus instances scrape simultaneously.

Readings in PowerIQ v9.x are returned inline with each resource (no separate `/readings` endpoints). The location hierarchy (data center, rack) is resolved by fetching `/racks` and `/data_centers` each poll cycle to build a lookup table.

If a poll fails, stale data is served and `poweriq_scrape_success` is set to `0`.

## Examples

The `examples/` directory contains reference files you can import into your own stack:

- `prometheus.yml` — Prometheus scrape configuration
- `grafana-alert-rules.yml` — Grafana alert rules for power and environmental monitoring

## Possible improvements

- **Door contact sensors**: PowerIQ supports `ContactClosureSensor` on PDUs with door handles or cabinet locks. The exporter already exposes these as `poweriq_sensor_state{sensor_type="ContactClosureSensor"}` (1=triggered, 0=normal). If your PDUs have door contacts, they will appear automatically. You can alert with: `poweriq_sensor_state{sensor_type="ContactClosureSensor"} == 1`. Door events will also increment `poweriq_active_events{severity="CRITICAL"}`.
- **Per-rack power totals**: Sum active power across PDU-A and PDU-B per rack using PromQL (`sum by (rack) (poweriq_pdu_active_power_watts)`) to build capacity planning dashboards and billing reports.
- **Unutilized capacity**: If PDUs report rated amps, expose a utilization percentage metric per PDU.
- **Inlet pole readings**: Three-phase PDUs have per-pole current/voltage readings available via `inlet_pole_readings`. Currently only the aggregate inlet reading is exported.
- **Historical rollups**: The API provides hourly/daily/monthly rollup endpoints for outlets, inlets, sensors, and racks. These could power long-term trend dashboards without high-cardinality raw samples.

## License

MIT
