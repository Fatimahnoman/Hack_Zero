import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from golden_tier_external_world.config.enums import PlatformType
from golden_tier_external_world.config.settings import WatcherConfig
from golden_tier_external_world.config.loader import load_settings
from golden_tier_external_world.config.secrets import load_secrets, get_secret
from golden_tier_external_world.storage.file_storage import FileStorage
from golden_tier_external_world.storage.backends.json_backend import JsonBackend
from golden_tier_external_world.storage.vaults.seen_vault import SeenVault
from golden_tier_external_world.events.bus import ProductionEventBus
from golden_tier_external_world.watchers.linkedin import LinkedInWatcher
from golden_tier_external_world.planner import Planner
from golden_tier_external_world.monitoring.metrics import MetricsCollector
from golden_tier_external_world.monitoring.health import HealthRegistry, HealthCheckResult, HealthStatus
from golden_tier_external_world.monitoring.http_server import HealthServer
from golden_tier_external_world.process_manager.manager import ProcessManager
from golden_tier_external_world.content_orchestrator.engine import ContentEngine
from golden_tier_external_world.content_orchestrator.generator import ContentGenerator
from golden_tier_external_world.content_orchestrator.dedup import ContentDedup
from golden_tier_external_world.content_orchestrator.queue import ContentQueue
from golden_tier_external_world.content_orchestrator.validator import ContentValidator
from golden_tier_external_world.content_orchestrator.rate_limiter import RateLimiter
from golden_tier_external_world.core.circuit_breaker import CircuitBreaker
from golden_tier_external_world.log_utils.structured_logger import setup_logging, get_logger, set_correlation_id


logger = get_logger("Runner")


class App:
    def __init__(
        self,
        vault_root: Path,
        config_path: Optional[Path] = None,
        env_file: Optional[Path] = None,
        health_port: int = 9090,
        json_logging: bool = True,
    ) -> None:
        self._vault_root = vault_root
        self._running = False

        vault_root.mkdir(parents=True, exist_ok=True)

        # ── Settings and secrets ──────────────────────────────
        load_secrets(env_file=env_file, override_environ=True)
        self.settings = load_settings(vault_root, config_path=config_path)

        # ── Structured logging ────────────────────────────────
        log_level = getattr(logging, self.settings.log_level.upper(), logging.INFO)
        setup_logging(level=log_level, json_format=json_logging)

        # ── Storage and events ─────────────────────────────────
        self.storage = FileStorage(vault_root / "data")
        self.metrics = MetricsCollector()
        self.event_bus = ProductionEventBus(max_workers=self.settings.max_workers)

        # ── Health check registry ──────────────────────────────
        self.health_registry = HealthRegistry()
        self._register_health_checks()

        # ── Content queue and dedup ────────────────────────────
        queue_backend = JsonBackend(vault_root / "content_queue.json")
        dlq_backend = JsonBackend(vault_root / "content_dlq.json")
        content_queue = ContentQueue(backend=queue_backend, dlq_backend=dlq_backend)

        dedup_seen_backend = JsonBackend(vault_root / "content_dedup.json")
        dedup_seen_vault = SeenVault(dedup_seen_backend)
        content_dedup = ContentDedup(dedup_seen_vault)

        # ── Content generation pipeline ────────────────────────
        gen = ContentGenerator(
            openai_api_key=get_secret("OPENAI_API_KEY"),
        )
        validator = ContentValidator()
        rate_limiter = RateLimiter()

        # ── Circuit breakers for platform posters ──────────────
        self._circuit_breakers: dict[str, CircuitBreaker] = {
            p.value: CircuitBreaker(name=f"poster:{p.value}")
            for p in [PlatformType.FACEBOOK, PlatformType.TWITTER, PlatformType.INSTAGRAM]
        }

        # ── Process manager ────────────────────────────────────
        self.process_manager = ProcessManager(
            vault_root=vault_root / "workers",
        )

        # ── Content engine ─────────────────────────────────────
        self.content_engine = ContentEngine(
            storage=self.storage,
            event_bus=self.event_bus,
            metrics=self.metrics,
            generator=gen,
            dedup=content_dedup,
            queue=content_queue,
            process_manager=self.process_manager,
            validator=validator,
            rate_limiter=rate_limiter,
        )

        # ── Planner ────────────────────────────────────────────
        self.planner = Planner(
            storage=self.storage,
            event_bus=self.event_bus,
            metrics=self.metrics,
            content_engine=self.content_engine,
        )
        self.content_engine.set_planner_callback(self.planner.content_callback)

        # ── LinkedIn watcher ───────────────────────────────────
        seen_backend = JsonBackend(vault_root / "linkedin_seen.json")
        seen_vault = SeenVault(seen_backend)

        watcher_config = WatcherConfig(
            platform=PlatformType.LINKEDIN,
            poll_interval_seconds=60,
        )

        self.watcher = LinkedInWatcher(
            config=watcher_config,
            storage=self.storage,
            event_bus=self.event_bus,
            seen_vault=seen_vault,
        )

        # ── Health HTTP server ─────────────────────────────────
        self.health_server = HealthServer(
            health_registry=self.health_registry,
            metrics_collector=self.metrics,
            port=health_port,
        )

        logger.info(
            "App initialized | vault=%s | watchers=%s | health_port=%d",
            vault_root, [PlatformType.LINKEDIN.value], health_port,
        )

    def _register_health_checks(self) -> None:
        self.health_registry.register("event_bus", lambda: self._check_event_bus())
        self.health_registry.register("process_manager", lambda: self._check_process_manager())
        self.health_registry.register("watcher_linkedin", lambda: self._check_watcher())
        self.health_registry.register("content_engine", lambda: self._check_content_engine())

    def _check_event_bus(self) -> HealthCheckResult:
        running = getattr(self.event_bus, "_running", False)
        return HealthCheckResult(
            name="event_bus",
            status=HealthStatus.HEALTHY if running else HealthStatus.UNHEALTHY,
            message="Event bus running" if running else "Event bus not started",
            details={"max_workers": self.event_bus._max_workers} if hasattr(self.event_bus, "_max_workers") else {},
        )

    def _check_process_manager(self) -> HealthCheckResult:
        running = self.process_manager.is_running
        active = self.process_manager.active_workers
        return HealthCheckResult(
            name="process_manager",
            status=HealthStatus.HEALTHY if running and active > 0 else HealthStatus.DEGRADED if running else HealthStatus.UNHEALTHY,
            message=f"Process manager running={running}, active_workers={active}",
            details={"running": running, "active_workers": active},
        )

    def _check_watcher(self) -> HealthCheckResult:
        return HealthCheckResult(
            name="watcher_linkedin",
            status=HealthStatus.HEALTHY,
            message="Watcher configured",
        )

    def _check_content_engine(self) -> HealthCheckResult:
        running = self.content_engine.is_running if hasattr(self.content_engine, "is_running") else False
        return HealthCheckResult(
            name="content_engine",
            status=HealthStatus.HEALTHY if running else HealthStatus.DEGRADED,
            message="Content engine running" if running else "Content engine not started",
            details={"queue_pending": self.content_engine.queue.pending_count()},
        )

    def start(self) -> None:
        set_correlation_id(f"app-{int(time.time())}")
        self._running = True
        self.event_bus.start()
        self.health_server.start()
        self.process_manager.start_all()
        self.planner.register()

        logger.info("Starting watcher for %s", self.watcher.platform.value)
        self.watcher.start()

        logger.info(
            "App running | vault=%s | health=%s | circuit_breakers=%s",
            self._vault_root,
            f"http://127.0.0.1:{self.health_server.port}/healthz",
            list(self._circuit_breakers.keys()),
        )

    def stop(self) -> None:
        self._running = False
        logger.info("Shutting down...")

        self.watcher.stop()
        self.planner.shutdown()
        self.process_manager.stop_all()
        self.health_server.stop()
        self.event_bus.stop()

        snap = self.metrics.snapshot()
        logger.info(
            "Final metrics: published=%d handled=%d errors=%d by_type=%s",
            snap["total_published"],
            snap["total_handled"],
            snap["total_errors"],
            snap["by_event_type"],
        )
        logger.info("Shutdown complete")

    def wait(self) -> None:
        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Golden Tier Social Media Content Orchestrator",
    )
    parser.add_argument(
        "--vault-root", type=Path, default=Path("vault"),
        help="Root directory for data storage (default: vault)",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to JSON config file",
    )
    parser.add_argument(
        "--env-file", type=Path, default=None,
        help="Path to .env file for secrets",
    )
    parser.add_argument(
        "--health-port", type=int, default=9090,
        help="Port for health/metrics HTTP server (default: 9090)",
    )
    parser.add_argument(
        "--json-logging", action="store_true", default=True,
        help="Enable JSON structured logging (default: true)",
    )
    parser.add_argument(
        "--text-logging", action="store_false", dest="json_logging",
        help="Disable JSON structured logging, use plain text",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    app = App(
        vault_root=args.vault_root,
        config_path=args.config,
        env_file=args.env_file,
        health_port=args.health_port,
        json_logging=args.json_logging,
    )

    health = app.health_registry

    def _signal_handler(signum: int, _frame: object) -> None:
        logger.info("Signal %d received", signum)
        snap = health.aggregate()
        logger.info("Final health: status=%s", snap.get("status"))
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    app.start()
    app.wait()


if __name__ == "__main__":
    main()
