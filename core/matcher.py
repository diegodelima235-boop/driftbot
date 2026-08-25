"""Decide se um anuncio interessa e o quanto interessa."""
from __future__ import annotations

import functools
import logging
import re

from .drift import detectar_paddles, detectar_projeto
from .models import Listing, norm

log = logging.getLogger("driftbot.matcher")


@functools.lru_cache(maxsize=2048)
def _kw_regex(kw: str) -> re.Pattern:
    """Palavra-chave tem que comecar em inicio de palavra.

    Sem isso, casar por pedaco de texto gera falso positivo grave:
      'tt'   casava dentro de jeTTa, corveTTe, aTTractive  -> VW virava Audi
      'capa' casava dentro de esCAPAmento -> anuncio de drift era bloqueado
    O `[a-z]?` final aceita uma letra colada, porque a OLX escreve '118iA 2.0'
    e o plural das palavras bloqueadas ('sucatas').
    """
    return re.compile(r"\b" + re.escape(kw) + r"[a-z]?\b")


def _tem(hay: str, kw: str) -> bool:
    return bool(kw) and _kw_regex(kw).search(hay) is not None


class Matcher:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.targets = cfg["targets"]
        self.block = [norm(b) for b in cfg.get("blocklist", [])]
        self.auction_kw = [norm(k) for k in cfg.get("auction_keywords", [])]

        f = cfg.get("filtros", {})
        # Automatico so entra se tiver borboleta/Steptronic. Manual entra sempre.
        # Anuncio sem info de cambio NAO e descartado - seria pior perder um manual
        # por falta de dado do que deixar passar um automatico.
        self.exigir_troca_manual = bool(f.get("exigir_troca_manual", True))
        # Se True, descarta carro "de familia" e so mostra projeto/batido/preparado.
        self.so_projetos = bool(f.get("somente_projetos"))

        b = cfg["budget"]
        self.min_price, self.max_price = b["min_price"], b["max_price"]
        self.peso_preco = b.get("peso_preco", 20)
        self.fracao_barganha = b.get("fracao_barganha", 0.55)
        y = cfg["year"]
        self.min_year, self.max_year = y["min"], y["max"]

        r = cfg["region"]
        self.perto = [norm(c) for c in r.get("cidades_perto", [])]
        self.medio = [norm(c) for c in r.get("cidades_medio", [])]
        self.uf = (r.get("uf") or "").upper()

    # ------------------------------------------------------------------
    def evaluate(self, li: Listing) -> Listing | None:
        """Devolve o Listing enriquecido (score/tier/flags) ou None se rejeitado."""
        hay = norm(f"{li.title}")

        # 1. blocklist - sucata, so peca, so documento...
        for b in self.block:
            if _tem(hay, b):
                return None

        # 2. tem que bater com algum alvo
        tgt = self._match_target(hay)
        if not tgt:
            return None
        li.matched_target = tgt["name"]
        li.tier = tgt.get("tier", "C")

        # 3. cambio. Manual sempre passa. Automatico so passa se der pra trocar
        # marcha na mao (borboleta/Steptronic) - automatico puro nao serve.
        li.paddles = detectar_paddles(li.title, li.description, li.gearbox, li.year)
        if li.gearbox == "automatico" and self.exigir_troca_manual and li.paddles == "nao":
            return None

        li.is_project = detectar_projeto(
            li.title, li.description, leilao=li.is_auction, km=li.km
        )
        if self.so_projetos and not li.is_project:
            return None

        # 4. preco
        if li.price is not None:
            if li.price > self.max_price or li.price < self.min_price:
                return None
        # preco None ("a combinar") passa: costuma ser leilao/batido, que e o alvo

        # 4. ano - se nao tiver ano no titulo, nao rejeita (leilao raramente traz)
        if li.year is None:
            li.year = None
        elif not (self.min_year <= li.year <= self.max_year):
            return None

        # 5. etiquetas leilao/batido
        li.is_auction = li.is_auction or any(_tem(hay, k) for k in self.auction_kw)
        li.is_damaged = li.is_auction

        li.score, li.reasons = self._score(li, hay)
        return li

    # ------------------------------------------------------------------
    def _match_target(self, hay: str) -> dict | None:
        for t in self.targets:
            must_any = [norm(m) for m in t.get("must_any", [])]
            if not any(_tem(hay, m) for m in must_any):
                continue
            must_not = [norm(m) for m in t.get("must_not", [])]
            if any(_tem(hay, m) for m in must_not):
                continue
            return t
        return None

    # ------------------------------------------------------------------
    def _score(self, li: Listing, hay: str) -> tuple[int, list[str]]:
        """0-100. So pra ordenar o que chega primeiro no Telegram."""
        score = 40
        why: list[str] = []

        # Tier do alvo. Desde que a Audi saiu (regra "so traccao traseira") nao existe
        # mais nenhum alvo tier C; a penalidade fica valendo caso um dia entre algum
        # alvo mais fraco, pra ele nunca competir com um drift car de verdade.
        score += {"A": 25, "B": 10, "C": -20}.get(li.tier, 0)

        # proximidade - o fator que mais importa pra ir ver o carro
        city = norm(li.city)
        if city and any(c in city or city in c for c in self.perto):
            score += 25
            why.append("perto de voce")
        elif city and any(c in city or city in c for c in self.medio):
            score += 10
            why.append("regiao media")
        elif li.uf and li.uf.upper() != self.uf:
            score -= 20
            why.append(f"fora de {self.uf}")

        # Preco: o criterio que ele mais valoriza ("foco e ter os mais em conta").
        # O peso vem do config porque tem que subir junto com o teto - senao, com
        # teto alto, um carro de 95k fica quase empatado com um de 30k.
        if li.price:
            folga = 1 - (li.price / self.max_price)
            score += int(folga * self.peso_preco)
            if li.price <= self.max_price * self.fracao_barganha:
                score += 5
                why.append("EM CONTA")
        else:
            why.append("preco sob consulta")

        # Cambio: manual e o que ele quer de verdade pra drift. Automatico com
        # borboleta entra, mas nunca deve ganhar de um manual equivalente.
        if li.gearbox == "manual":
            score += 15
            why.append("MANUAL")
        elif li.paddles == "sim":
            score += 2
            why.append("automatico com borboleta")
        elif li.paddles == "provavel":
            score -= 6
            why.append("automatico, borboleta a confirmar")

        # Projeto e o foco declarado dele
        if li.is_project:
            score += 12
            why.append("PROJETO")
        if li.is_auction:
            why.append("leilao/batido")

        return max(0, min(100, score)), why
