from __future__ import annotations

from http.server import HTTPServer, BaseHTTPRequestHandler
from json import dumps
from pathlib import Path
from threading import Thread, Event
from typing import Any, Optional
import logging

from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.monitoring.health import HealthRegistry


_LOGGER = logging.getLogger("http_server")


class _HealthHandler(BaseHTTPRequestHandler):
    health_registry: Optional[HealthRegistry] = None
    metrics_collector: Optional[MetricsCollector] = None

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._handle_liveness()
        elif self.path == "/ready":
            self._handle_readiness()
        elif self.path == "/metrics":
            self._handle_metrics()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not_found"}')

    def _handle_liveness(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dumps({"status": "alive"}).encode())

    def _handle_readiness(self) -> None:
        if self.health_registry is None:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(dumps({"status": "unhealthy", "error": "no_health_registry"}).encode())
            return

        agg = self.health_registry.aggregate()
        status_code = 200 if agg["status"] == "healthy" else 503
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dumps(agg).encode())

    def _handle_metrics(self) -> None:
        if self.metrics_collector is None:
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(dumps({"error": "no_metrics_collector"}).encode())
            return

        snap = self.metrics_collector.snapshot()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(dumps(snap).encode())

    def log_message(self, format: str, *args: Any) -> None:
        _LOGGER.debug("HTTP | %s", format % args)


class HealthServer:
    def __init__(
        self,
        health_registry: HealthRegistry,
        metrics_collector: MetricsCollector,
        host: str = "127.0.0.1",
        port: int = 9090,
    ) -> None:
        self._health_registry = health_registry
        self._metrics_collector = metrics_collector
        self._host = host
        self._port = port

        _HealthHandler.health_registry = health_registry
        _HealthHandler.metrics_collector = metrics_collector

        self._server: Optional[HTTPServer] = None
        self._thread: Optional[Thread] = None
        self._stop_event = Event()
        self._logger = logging.getLogger(self.__class__.__name__)

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        if self._server is not None:
            return

        try:
            self._server = HTTPServer(
                (self._host, self._port),
                _HealthHandler,
            )
            self._server.timeout = 1.0
            _, actual_port = self._server.server_address
            self._port = actual_port

            self._thread = Thread(
                target=self._serve,
                name="health-server",
                daemon=True,
            )
            self._thread.start()
            self._logger.info(
                "Health server started | host=%s | port=%d",
                self._host, self._port,
            )
        except OSError as e:
            self._logger.error(
                "Failed to start health server | host=%s | port=%d | error=%s",
                self._host, self._port, e,
            )

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            if self._server:
                self._server.handle_request()

    def stop(self) -> None:
        self._stop_event.set()
        if self._server:
            self._server.server_close()
            self._server = None
        self._logger.info("Health server stopped")
