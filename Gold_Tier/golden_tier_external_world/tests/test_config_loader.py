from __future__ import annotations

from pathlib import Path
from unittest import TestCase, mock
import json
import os
import tempfile

from golden_tier_external_world.config.loader import load_settings, _to_bool, _set_nested_attr
from golden_tier_external_world.config.settings import AppSettings


class TestConfigLoader(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.vault_root = self.tmp_dir / "vault"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_load_settings_defaults(self) -> None:
        settings = load_settings(self.vault_root)
        self.assertEqual(settings.vault_path, self.vault_root)
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.max_workers, 4)

    def test_load_settings_from_config_file(self) -> None:
        config_data = {
            "log_level": "DEBUG",
            "max_workers": 8,
            "content": {
                "queue_poll_interval": 5.0,
                "default_max_retries": 5,
                "enable_validation": False,
            },
        }
        config_path = self.tmp_dir / "config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        settings = load_settings(self.vault_root, config_path=config_path)
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.max_workers, 8)
        self.assertEqual(settings.content.queue_poll_interval, 5.0)
        self.assertEqual(settings.content.default_max_retries, 5)
        self.assertFalse(settings.content.enable_validation)

    def test_load_settings_from_env(self) -> None:
        with mock.patch.dict(os.environ, {
            "GT_LOG_LEVEL": "ERROR",
            "GT_MAX_WORKERS": "16",
            "GT_QUEUE_POLL_INTERVAL": "3.0",
            "GT_ENABLE_VALIDATION": "false",
            "GT_DLQ_REPLAY_INTERVAL": "20",
        }, clear=False):
            settings = load_settings(self.vault_root)
            self.assertEqual(settings.log_level, "ERROR")
            self.assertEqual(settings.max_workers, 16)
            self.assertEqual(settings.content.queue_poll_interval, 3.0)
            self.assertFalse(settings.content.enable_validation)
            self.assertEqual(settings.content.dlq_replay_interval, 20)

    def test_load_settings_config_overrides_defaults(self) -> None:
        config_data = {
            "content": {
                "target_platforms": ["facebook", "twitter"],
                "rate_limits": {
                    "facebook": [500, 86400],
                },
                "platform_char_limits": {
                    "twitter": 100,
                },
            },
        }
        config_path = self.tmp_dir / "config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        settings = load_settings(self.vault_root, config_path=config_path)
        self.assertEqual(settings.content.target_platforms, ["facebook", "twitter"])
        self.assertEqual(settings.content.rate_limits.get("facebook"), [500, 86400])
        self.assertEqual(settings.content.platform_char_limits.get("twitter"), 100)

    def test_load_settings_invalid_config_file(self) -> None:
        config_path = self.tmp_dir / "invalid.json"
        config_path.write_text("not json", encoding="utf-8")

        settings = load_settings(self.vault_root, config_path=config_path)
        self.assertEqual(settings.log_level, "INFO")

    def test_config_file_not_found(self) -> None:
        settings = load_settings(self.vault_root, config_path=Path("/nonexistent/config.json"))
        self.assertEqual(settings.log_level, "INFO")

    def test_content_config_respond_to(self) -> None:
        config_data = {
            "content": {
                "respond_to": ["MESSAGE", "COMMENT"],
            },
        }
        config_path = self.tmp_dir / "config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        settings = load_settings(self.vault_root, config_path=config_path)
        self.assertEqual(settings.content.respond_to, {"MESSAGE", "COMMENT"})

    def test_env_bool_parsing(self) -> None:
        self.assertTrue(_to_bool("true"))
        self.assertTrue(_to_bool("1"))
        self.assertTrue(_to_bool("yes"))
        self.assertTrue(_to_bool("on"))
        self.assertFalse(_to_bool("false"))
        self.assertFalse(_to_bool("0"))
        self.assertFalse(_to_bool("no"))
        self.assertFalse(_to_bool("off"))

    def test_set_nested_attr(self) -> None:
        settings = AppSettings.defaults(self.vault_root)
        _set_nested_attr(settings, "content.queue_poll_interval", 10.0)
        self.assertEqual(settings.content.queue_poll_interval, 10.0)

    def test_credentials_in_config(self) -> None:
        config_data = {
            "credentials": {
                "twitter": {
                    "access_token": "tw-token-123",
                    "username": "bot_user",
                },
            },
        }
        config_path = self.tmp_dir / "config.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")

        with mock.patch("golden_tier_external_world.config.loader.get_secret", return_value=None):
            settings = load_settings(self.vault_root, config_path=config_path)

        from golden_tier_external_world.config.enums import PlatformType
        cred = settings.credentials.get(PlatformType.TWITTER)
        self.assertIsNotNone(cred)
