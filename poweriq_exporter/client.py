"""Sunbird PowerIQ REST API client with Basic Auth and pagination.

Readings are embedded inline in the list responses (e.g. each PDU object
contains a ``reading`` key with ``inlet_readings``).  The API uses
``limit`` / ``offset`` for pagination (not ``page`` / ``per_page``).
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100


class PowerIQClient:
    """Thin wrapper around the PowerIQ REST API (v2)."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        api_base: str = "/api/v2",
        ca_bundle: str | bool = True,
    ) -> None:
        self.base_url = f"https://{host}{api_base}"
        self._auth = (username, password)

        self._http = requests.Session()
        self._http.verify = ca_bundle
        self._http.auth = self._auth
        self._http.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[502, 503, 504])
        self._http.mount("https://", HTTPAdapter(max_retries=retry))

    # ------------------------------------------------------------------
    # Generic request helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a single resource."""
        url = f"{self.base_url}{path}"
        resp = self._http.get(url, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()

    def _get_all(self, path: str, key: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Paginate through a collection using ``limit`` / ``offset``.

        The *key* argument is the JSON key that holds the list of items
        (e.g. ``"pdus"``, ``"outlets"``).

        Returns an empty list if the endpoint does not exist (404).
        """
        params = dict(params or {})
        offset = 0

        all_items: list[dict] = []
        while True:
            params["limit"] = _PAGE_SIZE
            params["offset"] = offset
            try:
                data = self._get(path, params)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    logger.info("Endpoint %s not available (404), skipping", path)
                    return []
                raise
            items = data.get(key, [])
            if not items:
                break
            all_items.extend(items)
            if len(items) < _PAGE_SIZE:
                break
            offset += len(items)
        return all_items

    # ------------------------------------------------------------------
    # Resource endpoints
    # ------------------------------------------------------------------

    def get_data_centers(self) -> list[dict]:
        return self._get_all("/data_centers", "data_centers")

    def get_racks(self) -> list[dict]:
        return self._get_all("/racks", "racks")

    def get_pdus(self) -> list[dict]:
        """Return all PDUs.  Each PDU includes inline ``reading.inlet_readings``."""
        return self._get_all("/pdus", "pdus")

    def get_outlets(self) -> list[dict]:
        """Return all outlets.  Each outlet includes inline ``reading``."""
        return self._get_all("/outlets", "outlets")

    def get_sensors(self) -> list[dict]:
        """Return all sensors.  Each sensor includes inline ``reading`` and ``state``."""
        return self._get_all("/sensors", "sensors")

    def get_circuit_breakers(self) -> list[dict]:
        return self._get_all("/circuit_breakers", "circuit_breakers")

    def get_events(self) -> list[dict]:
        """Return all events.  Caller should filter by ``cleared_at is None``."""
        return self._get_all("/events", "events")
