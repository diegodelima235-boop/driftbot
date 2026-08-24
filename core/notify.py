"""Envio pro Telegram (API oficial, gratuita, sem limite pratico)."""
from __future__ import annotations

import html
import logging
import time

from .models import Listing

log = logging.getLogger("driftbot.notify")

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str, chat_id: str, http):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.http = http
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            log.warning("Telegram desligado: falta bot_token/chat_id no config.yaml")

    # ------------------------------------------------------------------
    def _call(self, method: str, params: dict) -> bool:
        if not self.enabled:
            return False
        url = API.format(token=self.token, method=method)
        r = self.http.get(url, params=params, tries=2)
        if r is None:
            return False
        try:
            ok = r.json().get("ok", False)
            if not ok:
                log.warning("Telegram recusou: %s", r.text[:200])
            return ok
        except Exception:
            return False

    # ------------------------------------------------------------------
    def send_text(self, text: str) -> bool:
        return self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )

    # ------------------------------------------------------------------
    def send_listing(self, li: Listing, reason: str) -> bool:
        e = html.escape
        flag = "\U0001f6a8 " if li.is_auction else ""
        proj = "\U0001f527 " if li.is_project else ""
        tier_icon = {"A": "\U0001f525", "B": "✅", "C": "ℹ️"}.get(li.tier, "")
        flag = proj + flag

        linhas = [
            f"{flag}{tier_icon} <b>{e(li.title[:110])}</b>",
            "",
            f"\U0001f4b0 <b>{e(li.price_fmt)}</b>" + (f"   • <i>{e(reason)}</i>" if reason != "novo" else ""),
            f"\U0001f4cd {e(li.local_fmt)}",
        ]
        if li.year:
            linhas.append(f"\U0001f4c5 {li.year}")
        if li.km:
            linhas.append(f"\U0001f6e3 {li.km:,} km".replace(",", "."))
        if li.gearbox == "manual":
            linhas.append("⚙️ <b>Manual</b>")
        elif li.gearbox == "automatico":
            extra = {
                "sim": " <b>com borboleta</b>",
                "provavel": " — borboleta/Steptronic <i>a confirmar no anuncio</i>",
            }.get(li.paddles, "")
            linhas.append(f"⚙️ Automatico{extra}")
        else:
            linhas.append("⚙️ Cambio nao informado")

        linhas.append(f"\U0001f3af {e(li.matched_target)}  •  tier {li.tier}  •  score {li.score}")
        if li.reasons:
            linhas.append(f"\U0001f4a1 {e(', '.join(li.reasons))}")
        if li.is_auction:
            linhas.append("\n⚠️ <i>Marcado como leilao/batido - confira chassi, "
                          "monta e se o documento nao esta baixado.</i>")
        linhas.append(f"\n\U0001f517 <a href=\"{e(li.url)}\">Ver anuncio ({e(li.source)})</a>")

        caption = "\n".join(linhas)

        # Tenta com foto (fica bem melhor); se falhar, manda texto
        if li.image:
            ok = self._call(
                "sendPhoto",
                {
                    "chat_id": self.chat_id,
                    "photo": li.image,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
            )
            if ok:
                return True
        return self.send_text(caption[:4096])

    # ------------------------------------------------------------------
    def send_batch(self, items: list[tuple[Listing, str]], pause: float = 1.2) -> list[Listing]:
        """Devolve os que REALMENTE foram enviados.

        Devolver a lista (e nao a contagem) importa: se o 3o de 12 falhar, contar
        e fatiar `fila[:n]` marcaria o item errado como notificado, e o que falhou
        nunca mais seria reenviado.
        """
        enviados: list[Listing] = []
        for li, reason in items:
            if self.send_listing(li, reason):
                enviados.append(li)
            time.sleep(pause)  # Telegram aguenta ~30 msg/s, mas 1/s e educado
        return enviados
