import logging
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from golden_tier_external_world.config.secrets import get_secret

_LOGGER = logging.getLogger("captcha")


class ReCaptchaSolver:
    BASE_URL = "https://2captcha.com"

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or get_secret("CAPTCHA_API_KEY")
        if not self._api_key:
            _LOGGER.warning("No CAPTCHA_API_KEY configured — captcha solving disabled")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def solve_recaptcha_v2(
        self, sitekey: str, page_url: str, timeout: int = 120
    ) -> Optional[str]:
        if not self._api_key:
            _LOGGER.warning("Cannot solve reCAPTCHA: no API key")
            return None

        _LOGGER.info("Sending reCAPTCHA v2 to 2Captcha | sitekey=%s", sitekey[:8])

        resp = requests.post(
            f"{self.BASE_URL}/in.php",
            data={
                "key": self._api_key,
                "method": "userrecaptcha",
                "googlekey": sitekey,
                "pageurl": page_url,
                "json": 1,
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("status") != 1:
            _LOGGER.error("2Captcha in.php failed | error=%s", data.get("request"))
            return None

        captcha_id = data["request"]
        _LOGGER.info("Captcha submitted | id=%s...", captcha_id[:16])

        start = time.time()
        while time.time() - start < timeout:
            time.sleep(5)
            resp = requests.get(
                f"{self.BASE_URL}/res.php",
                params={
                    "key": self._api_key,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                },
                timeout=30,
            )
            data = resp.json()
            if data.get("status") == 1:
                token = data["request"]
                _LOGGER.info("Captcha solved | elapsed=%.1fs", time.time() - start)
                return token
            if data.get("request") != "CAPCHA_NOT_READY":
                _LOGGER.error("2Captcha res.php error | error=%s", data.get("request"))
                return None

        _LOGGER.error("Captcha solving timed out after %ds", timeout)
        return None

    def solve_recaptcha_v3(
        self, sitekey: str, page_url: str, action: str = "verify", min_score: float = 0.3, timeout: int = 120
    ) -> Optional[str]:
        if not self._api_key:
            return None

        _LOGGER.info("Sending reCAPTCHA v3 to 2Captcha")

        resp = requests.post(
            f"{self.BASE_URL}/in.php",
            data={
                "key": self._api_key,
                "method": "userrecaptcha",
                "version": "v3",
                "googlekey": sitekey,
                "pageurl": page_url,
                "action": action,
                "min_score": min_score,
                "json": 1,
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("status") != 1:
            _LOGGER.error("2Captcha v3 in.php failed | error=%s", data.get("request"))
            return None

        captcha_id = data["request"]

        start = time.time()
        while time.time() - start < timeout:
            time.sleep(5)
            resp = requests.get(
                f"{self.BASE_URL}/res.php",
                params={
                    "key": self._api_key,
                    "action": "get",
                    "id": captcha_id,
                    "json": 1,
                },
                timeout=30,
            )
            data = resp.json()
            if data.get("status") == 1:
                token = data["request"]
                _LOGGER.info("Captcha v3 solved | elapsed=%.1fs", time.time() - start)
                return token
            if data.get("request") != "CAPCHA_NOT_READY":
                _LOGGER.error("2Captcha v3 error | error=%s", data.get("request"))
                return None

        _LOGGER.error("Captcha v3 timed out after %ds", timeout)
        return None

    def detect_and_solve(self, page, timeout: int = 120) -> Optional[str]:
        if not self._api_key:
            return None

        sitekey = self._detect_sitekey(page)
        if not sitekey:
            _LOGGER.debug("No reCAPTCHA detected on page")
            return None

        page_url = page.url
        _LOGGER.info("Detected reCAPTCHA | sitekey=%s...", sitekey[:8])

        token = self.solve_recaptcha_v2(sitekey, page_url, timeout=timeout)
        if token:
            self._inject_token(page, token)
        return token

    @staticmethod
    def _detect_sitekey(page) -> Optional[str]:
        try:
            frames = page.frames
            for frame in frames:
                try:
                    el = frame.query_selector('div[data-sitekey]')
                    if el:
                        return el.get_attribute("data-sitekey")
                except Exception:
                    continue

            for frame in frames:
                try:
                    el = frame.query_selector('#g-recaptcha-response')
                    if el:
                        parent = frame.evaluate(
                            "() => document.querySelector('#g-recaptcha-response').closest('[data-sitekey]')"
                        )
                        if parent:
                            return parent.get("data-sitekey")
                except Exception:
                    continue

            sitekey = page.evaluate("""
                () => {
                    const el = document.querySelector('.g-recaptcha');
                    return el ? el.getAttribute('data-sitekey') : null;
                }
            """)
            if sitekey:
                return sitekey

            sitekey = page.evaluate("""
                () => {
                    const el = document.querySelector('[data-sitekey]');
                    return el ? el.getAttribute('data-sitekey') : null;
                }
            """)
            return sitekey
        except Exception as e:
            _LOGGER.debug("Failed to detect sitekey | error=%s", e)
            return None

    @staticmethod
    def _inject_token(page, token: str) -> None:
        try:
            page.evaluate("""
                (token) => {
                    const textarea = document.getElementById('g-recaptcha-response');
                    if (textarea) {
                        textarea.style.display = 'block';
                        textarea.value = token;
                    }
                    const callback = window.___grecaptcha_cfg?.clients?.[0]?.callback;
                    if (typeof callback === 'function') {
                        callback(token);
                    }
                }
            """, token)
            _LOGGER.info("reCAPTCHA token injected")
        except Exception as e:
            _LOGGER.warning("Failed to inject reCAPTCHA token | error=%s", e)
