"""Cliente HTTP que passa pelo Cloudflare.

Motivo do curl_cffi: OLX e Webmotors bloqueiam por *fingerprint TLS* (JA3), nao por
User-Agent. `requests` leva 403 mesmo com headers perfeitos; curl_cffi imita o
handshake do Chrome e passa.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time

import certifi

log = logging.getLogger("driftbot.http")

# Esta maquina tem SSL_CERT_FILE / SSL_CERT_DIR apontando para pastas do Warface
# (sobra de engenharia reversa). Isso sequestra a validacao TLS do curl_cffi e
# derruba tudo com "unable to get local issuer certificate". Neutralizamos aqui.
for _var in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
    _val = os.environ.get(_var)
    if _val and "certifi" not in _val.lower():
        log.debug("ignorando %s=%s (aponta fora do certifi)", _var, _val)
        os.environ.pop(_var, None)

CA_BUNDLE = certifi.where()

# A URL da API do Telegram CARREGA o token: api.telegram.org/bot<TOKEN>/sendMessage.
# Como este repositorio e publico, o log do Actions e o artefato driftbot.log ficam
# baixaveis por qualquer um - logar a URL crua vazaria o token no primeiro erro de
# rede. Toda URL passa por aqui antes de virar log.
_TOKEN_NA_URL = re.compile(r"(bot)\d{6,}:[A-Za-z0-9_\-]{20,}", re.I)


def safe_url(url: str, limite: int = 70) -> str:
    return _TOKEN_NA_URL.sub(r"\1<REDACTED>", url)[:limite]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Cache-Control": "no-cache",
}


class Http:
    """Wrapper com retry, backoff e jitter entre requisicoes."""

    def __init__(self, timeout: int = 35, min_delay: float = 1.5, max_delay: float = 4.0):
        from curl_cffi import requests as cr

        self._cr = cr
        self.timeout = timeout
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_call = 0.0

    def _throttle(self) -> None:
        """Espaca as chamadas pra nao parecer bot e nao tomar rate-limit."""
        elapsed = time.time() - self._last_call
        wait = random.uniform(self.min_delay, self.max_delay) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def get(self, url: str, *, headers: dict | None = None, tries: int = 3, **kw):
        h = dict(BASE_HEADERS)
        if headers:
            h.update(headers)

        last_err: Exception | None = None
        for attempt in range(1, tries + 1):
            self._throttle()
            try:
                r = self._cr.get(
                    url,
                    impersonate="chrome",
                    timeout=self.timeout,
                    verify=CA_BUNDLE,
                    headers=h,
                    **kw,
                )
                r.encoding = "utf-8"
                if r.status_code == 200:
                    return r
                # 403/429 = provavelmente anti-bot; espera mais e tenta de novo
                if r.status_code in (403, 429, 503) and attempt < tries:
                    backoff = 5 * attempt + random.uniform(0, 3)
                    log.warning("%s -> %s, retry em %.1fs", safe_url(url), r.status_code, backoff)
                    time.sleep(backoff)
                    continue
                log.warning("%s -> HTTP %s", safe_url(url), r.status_code)
                return None
            except Exception as e:  # rede, DNS, TLS
                last_err = e
                if attempt < tries:
                    time.sleep(3 * attempt)
                    continue
        log.warning("%s falhou: %s", safe_url(url), type(last_err).__name__ if last_err else "?")
        return None

    def get_json(self, url: str, **kw):
        r = self.get(url, headers={"Accept": "application/json"}, **kw)
        if r is None:
            return None
        try:
            return r.json()
        except Exception:
            log.warning("resposta nao-JSON de %s", safe_url(url))
            return None
