from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock
import os
import tempfile

from golden_tier_external_world.config.secrets import (
    load_secrets,
    get_secret,
    redact,
    resolve_secrets_in_config,
    interpolate_env_vars,
)


class TestSecrets(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_env(self, content: str) -> Path:
        p = self.tmp_dir / ".env"
        p.write_text(content, encoding="utf-8")
        return p

    def test_load_secrets_from_env_file(self) -> None:
        env_file = self._write_env("OPENAI_API_KEY=sk-test-123\nTWITTER_BEARER_TOKEN=token-abc")
        secrets = load_secrets(env_file=env_file)
        self.assertEqual(secrets.get("OPENAI_API_KEY"), "sk-test-123")
        self.assertEqual(secrets.get("TWITTER_BEARER_TOKEN"), "token-abc")

    def test_load_secrets_env_file_not_found(self) -> None:
        secrets = load_secrets(env_file=Path("/nonexistent/.env"))
        self.assertEqual(secrets, {})

    def test_load_secrets_empty_lines_and_comments(self) -> None:
        env_file = self._write_env(
            "# This is a comment\n\nOPENAI_API_KEY=sk-key\n\n# Another comment\n"
        )
        secrets = load_secrets(env_file=env_file)
        self.assertEqual(secrets.get("OPENAI_API_KEY"), "sk-key")

    def test_load_secrets_from_environ(self) -> None:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-key"}, clear=False):
            secrets = load_secrets()
            self.assertEqual(secrets.get("OPENAI_API_KEY"), "sk-env-key")

    def test_get_secret_after_load(self) -> None:
        with mock.patch.dict(os.environ, {"TWITTER_BEARER_TOKEN": "env-token"}, clear=False):
            load_secrets()
            self.assertEqual(get_secret("TWITTER_BEARER_TOKEN"), "env-token")

    def test_get_secret_not_found(self) -> None:
        self.assertIsNone(get_secret("NONEXISTENT_KEY"))

    def test_get_secret_with_default(self) -> None:
        result = get_secret("NONEXISTENT_KEY", default="fallback")
        self.assertEqual(result, "fallback")

    def test_redact_long_value(self) -> None:
        redacted = redact("sk-test-api-key-abcdef")
        self.assertEqual(redacted, "sk-t****cdef")

    def test_redact_short_value(self) -> None:
        redacted = redact("abc12345")
        self.assertTrue("***" in redacted)

    def test_redact_none(self) -> None:
        self.assertEqual(redact(None), "None")

    def test_resolve_secrets_in_config(self) -> None:
        with mock.patch.dict(os.environ, {"MY_SECRET": "resolved-value"}, clear=False):
            config = {"api_key": "${MY_SECRET}", "name": "static"}
            result = resolve_secrets_in_config(config)
            self.assertEqual(result["api_key"], "resolved-value")
            self.assertEqual(result["name"], "static")

    def test_interpolate_env_vars(self) -> None:
        with mock.patch.dict(os.environ, {"HOST": "localhost", "PORT": "8080"}, clear=False):
            result = interpolate_env_vars("http://${HOST}:${PORT}/api")
            self.assertEqual(result, "http://localhost:8080/api")

    def test_interpolate_env_vars_missing(self) -> None:
        result = interpolate_env_vars("prefix-${MISSING_VAR}-suffix")
        self.assertIn("${MISSING_VAR}", result)

    def test_secrets_cached(self) -> None:
        secrets1 = load_secrets()
        secrets2 = load_secrets()
        self.assertIsNot(secrets1, secrets2)

    def test_env_file_override(self) -> None:
        env_file = self._write_env("MY_KEY=from_file")
        with mock.patch.dict(os.environ, {"MY_KEY": "from_env"}, clear=False):
            secrets = load_secrets(env_file=env_file, override_environ=True)
            self.assertIn("MY_KEY", secrets)
