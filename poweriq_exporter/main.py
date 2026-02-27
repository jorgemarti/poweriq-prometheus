"""Entrypoint for the Sunbird PowerIQ Prometheus exporter."""

from __future__ import annotations

import logging
import os
import signal
import sys

from prometheus_client import REGISTRY, start_http_server

from .collector import PowerIQCollector

def _init_logging() -> None:
    """Configure logging with level from LOG_LEVEL env var (default: INFO)."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # Keep urllib3 quiet unless we're in DEBUG mode
    if level > logging.DEBUG:
        logging.getLogger("urllib3").setLevel(logging.ERROR)


_init_logging()
logger = logging.getLogger(__name__)


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        logger.error("Required environment variable %s is not set", name)
        sys.exit(1)
    return val  # type: ignore[return-value]


def _build_client(dry_run: bool):
    if dry_run:
        from .fake import FakePowerIQClient
        logger.info("Running in --dry-run mode with simulated data")
        return FakePowerIQClient()

    from .client import PowerIQClient

    host = _env("PIQ_HOST", required=True)
    username = _env("PIQ_USERNAME", required=True)
    password = _env("PIQ_PASSWORD", required=True)
    api_base = _env("PIQ_API_BASE", "/api/v2")

    tls_ca = _env("TLS_CA_BUNDLE")
    tls_insecure = _env("TLS_INSECURE", "false").lower() == "true"
    if tls_insecure:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        ca_bundle: str | bool = False
    elif tls_ca:
        ca_bundle = tls_ca
    else:
        ca_bundle = True

    return PowerIQClient(
        host=host,
        username=username,
        password=password,
        api_base=api_base,
        ca_bundle=ca_bundle,
    )


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    scrape_interval = int(_env("PIQ_SCRAPE_INTERVAL", "60"))
    port = int(_env("EXPORTER_PORT", "9131"))

    client = _build_client(dry_run)

    collector = PowerIQCollector(client, scrape_interval=scrape_interval)
    REGISTRY.register(collector)
    collector.start()

    start_http_server(port)
    logger.info("Exporter listening on http://0.0.0.0:%d/metrics", port)

    stop = signal.Event() if hasattr(signal, "Event") else _make_stop_event()
    stop.wait()


def _make_stop_event():
    """Create a threading.Event that gets set on SIGINT/SIGTERM."""
    import threading
    evt = threading.Event()

    def _handler(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        evt.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
    return evt


if __name__ == "__main__":
    main()
