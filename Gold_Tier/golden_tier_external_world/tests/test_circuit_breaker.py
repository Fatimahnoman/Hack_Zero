from __future__ import annotations

from unittest import TestCase, mock
import time

from golden_tier_external_world.core.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitOpenError,
)


class TestCircuitBreaker(TestCase):
    def setUp(self) -> None:
        self.cb = CircuitBreaker(
            name="test",
            failure_threshold=3,
            recovery_timeout=0.1,
            half_open_max_calls=2,
            success_threshold=2,
        )

    def test_initial_state_closed(self) -> None:
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_successful_call_returns_result(self) -> None:
        result = self.cb.call(lambda: 42)
        self.assertEqual(result, 42)

    def test_failures_accumulate(self) -> None:
        with self.assertRaises(ValueError):
            self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        self.assertEqual(self.cb.failure_count, 1)
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_opens_after_threshold(self) -> None:
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        self.assertEqual(self.cb.state, CircuitState.OPEN)

    def test_open_circuit_raises(self) -> None:
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        with self.assertRaises(CircuitOpenError):
            self.cb.call(lambda: 42)

    def test_half_open_after_timeout(self) -> None:
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        time.sleep(0.15)
        result = self.cb.call(lambda: 42)
        self.assertEqual(result, 42)
        self.assertEqual(self.cb.state, CircuitState.HALF_OPEN)

    def test_half_open_success_resets(self) -> None:
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        time.sleep(0.15)
        self.cb.call(lambda: 42)
        self.cb.call(lambda: 42)

        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_half_open_failure_reopens(self) -> None:
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        time.sleep(0.15)
        self.cb.call(lambda: 42)

        with self.assertRaises(ValueError):
            self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        self.assertEqual(self.cb.state, CircuitState.OPEN)

    def test_half_open_max_calls_limited(self) -> None:
        cb = CircuitBreaker(
            name="test_limit",
            failure_threshold=3,
            recovery_timeout=0.01,
            half_open_max_calls=1,
            success_threshold=5,
        )
        for _ in range(3):
            with self.assertRaises(ValueError):
                cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        self.assertEqual(cb.state, CircuitState.OPEN)

        time.sleep(0.05)

        result = cb.call(lambda: 42)
        self.assertEqual(result, 42)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

        with self.assertRaises(CircuitOpenError) as ctx:
            cb.call(lambda: 99)

        self.assertIn("test_limit", str(ctx.exception))
        self.assertIn("half_open", str(ctx.exception))

    def test_reset_clears_state(self) -> None:
        for _ in range(3):
            with self.assertRaises(ValueError):
                self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        self.cb.reset()
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertEqual(self.cb.failure_count, 0)
        result = self.cb.call(lambda: 99)
        self.assertEqual(result, 99)

    def test_status_report(self) -> None:
        status = self.cb.status()
        self.assertEqual(status["name"], "test")
        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["failure_count"], 0)
        self.assertEqual(status["failure_threshold"], 3)

    def test_status_after_failure(self) -> None:
        with self.assertRaises(ValueError):
            self.cb.call(lambda: (_ for _ in ()).throw(ValueError("fail")))

        status = self.cb.status()
        self.assertEqual(status["failure_count"], 1)
        self.assertIsNotNone(status["last_failure_time"])

    def test_exception_preserves_original(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            self.cb.call(lambda: (_ for _ in ()).throw(RuntimeError("original")))

        self.assertEqual(str(ctx.exception), "original")

    def test_thread_safety(self) -> None:
        import concurrent.futures

        errors: list[Exception] = []

        def failing_call(i: int) -> int:
            if i % 2 == 0:
                raise ValueError(f"fail_{i}")
            return i

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(lambda: self.cb.call(lambda i=i: failing_call(i))) for i in range(20)]
            for f in concurrent.futures.as_completed(futures):
                try:
                    f.result()
                except (ValueError, CircuitOpenError) as e:
                    errors.append(e)

        self.assertGreater(len(errors), 0)
        self.assertIn(self.cb.state, (CircuitState.CLOSED, CircuitState.OPEN, CircuitState.HALF_OPEN))

    def test_default_params(self) -> None:
        cb = CircuitBreaker(name="defaults")
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.status()["failure_threshold"], 5)
        self.assertEqual(cb.status()["success_threshold"], 2)
