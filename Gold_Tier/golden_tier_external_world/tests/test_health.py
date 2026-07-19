from __future__ import annotations

from unittest import TestCase, mock
from golden_tier_external_world.monitoring.health import (
    HealthRegistry,
    HealthCheckResult,
    HealthStatus,
)


class TestHealthRegistry(TestCase):
    def setUp(self) -> None:
        self.registry = HealthRegistry()

    def test_register_and_run_check(self) -> None:
        self.registry.register("test_ok", lambda: HealthCheckResult(
            name="test_ok", status=HealthStatus.HEALTHY,
        ))
        result = self.registry.run_check("test_ok")
        self.assertIsNotNone(result)
        self.assertEqual(result.status, HealthStatus.HEALTHY)

    def test_run_nonexistent_check(self) -> None:
        result = self.registry.run_check("nonexistent")
        self.assertIsNone(result)

    def test_run_all_returns_all_results(self) -> None:
        self.registry.register("a", lambda: HealthCheckResult(name="a", status=HealthStatus.HEALTHY))
        self.registry.register("b", lambda: HealthCheckResult(name="b", status=HealthStatus.HEALTHY))
        results = self.registry.run_all()
        self.assertEqual(len(results), 2)
        self.assertIn("a", results)
        self.assertIn("b", results)

    def test_unregister_removes_check(self) -> None:
        self.registry.register("temp", lambda: HealthCheckResult(name="temp", status=HealthStatus.HEALTHY))
        self.registry.unregister("temp")
        self.assertIsNone(self.registry.run_check("temp"))

    def test_overall_status_healthy(self) -> None:
        self.registry.register("a", lambda: HealthCheckResult(name="a", status=HealthStatus.HEALTHY))
        self.registry.register("b", lambda: HealthCheckResult(name="b", status=HealthStatus.HEALTHY))
        self.registry.run_all()
        self.assertEqual(self.registry.overall_status(), HealthStatus.HEALTHY)

    def test_overall_status_degraded(self) -> None:
        self.registry.register("a", lambda: HealthCheckResult(name="a", status=HealthStatus.HEALTHY))
        self.registry.register("b", lambda: HealthCheckResult(name="b", status=HealthStatus.DEGRADED))
        self.registry.run_all()
        self.assertEqual(self.registry.overall_status(), HealthStatus.DEGRADED)

    def test_overall_status_unhealthy(self) -> None:
        self.registry.register("a", lambda: HealthCheckResult(name="a", status=HealthStatus.HEALTHY))
        self.registry.register("b", lambda: HealthCheckResult(name="b", status=HealthStatus.UNHEALTHY))
        self.registry.run_all()
        self.assertEqual(self.registry.overall_status(), HealthStatus.UNHEALTHY)

    def test_overall_status_empty(self) -> None:
        self.assertEqual(self.registry.overall_status(), HealthStatus.UNHEALTHY)

    def test_aggregate_format(self) -> None:
        self.registry.register("ok", lambda: HealthCheckResult(name="ok", status=HealthStatus.HEALTHY))
        agg = self.registry.aggregate()
        self.assertIn("status", agg)
        self.assertIn("checks", agg)
        self.assertEqual(agg["checks"]["ok"]["status"], "healthy")

    def test_check_exception_returns_unhealthy(self) -> None:
        def failing() -> HealthCheckResult:
            raise RuntimeError("check failed")

        self.registry.register("fail", failing)
        result = self.registry.run_check("fail")
        self.assertEqual(result.status, HealthStatus.UNHEALTHY)
        self.assertEqual(result.message, "check failed")

    def test_check_count(self) -> None:
        self.assertEqual(self.registry.check_count, 0)
        self.registry.register("a", lambda: HealthCheckResult(name="a", status=HealthStatus.HEALTHY))
        self.assertEqual(self.registry.check_count, 1)

    def test_registered_checks_list(self) -> None:
        self.registry.register("a", lambda: HealthCheckResult(name="a", status=HealthStatus.HEALTHY))
        self.registry.register("b", lambda: HealthCheckResult(name="b", status=HealthStatus.HEALTHY))
        self.assertListEqual(sorted(self.registry.registered_checks), ["a", "b"])


class TestHealthCheckResult(TestCase):
    def test_default_checked_at(self) -> None:
        result = HealthCheckResult(name="test", status=HealthStatus.HEALTHY)
        self.assertIsNotNone(result.checked_at)
        self.assertIn("T", result.checked_at)

    def test_default_details_empty(self) -> None:
        result = HealthCheckResult(name="test", status=HealthStatus.HEALTHY)
        self.assertEqual(result.details, {})

    def test_health_status_enum_values(self) -> None:
        self.assertEqual(HealthStatus.HEALTHY.value, "healthy")
        self.assertEqual(HealthStatus.DEGRADED.value, "degraded")
        self.assertEqual(HealthStatus.UNHEALTHY.value, "unhealthy")
