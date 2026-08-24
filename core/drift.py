"""Duas perguntas que nenhuma fonte responde direto: tem borboleta? e projeto de drift?

Borboleta: a OLX NAO tem esse campo. A lista de opcionais dela e fixa em 19 itens e
"Volante multifuncional" e controle de som, nao borboleta. Verificado em 2026-08-24.
Entao: quando ha texto do vendedor (Usadosbr) a gente le; quando nao ha (OLX), deduz
do modelo + ano, e marca como "provavel" pra ele conferir no anuncio.
"""
from __future__ import annotations

import re

from .models import norm

# ---------------------------------------------------------------------------
# Texto que confirma comando de marcha no volante/alavanca
PADDLE_KW = [
    "borboleta", "borboletas", "paddle", "paddle shift", "aleta", "aletas",
    "troca no volante", "trocas no volante", "marcha no volante",
    "steptronic", "tiptronic", "sequencial", "modo manual", "shift tronic",
    "s-tronic", "dsg", "e-shift", "manual mode",
]

# ---------------------------------------------------------------------------
# Modelos com cambio automatico que aceita troca manual (Steptronic e equivalentes).
# Regra por (padrao no titulo, ano minimo). Fora disso, automatico e so automatico.
MANUAL_MODE_TABLE = [
    # BMW Steptronic: chegou em 1996 e virou padrao nos automaticos daqui pra frente.
    # Borboleta mesmo so nos pacotes esportivos, por isso e "provavel", nao "sim".
    (r"\b(318|320|323|325|328|330)i", 1997),
    (r"\b(118|120|125|130)i", 2004),      # Serie 1 ja nasceu com Steptronic
    (r"\b(525|528|530|540)i", 1997),
    (r"\bz[34]\b", 2003),
    # Nissan
    (r"\b350z\b", 2003),
    (r"\b370z\b", 2009),
    (r"\bskyline\b|\br3[234]\b", 1993),
    # Toyota / Lexus - E-shift no volante
    (r"\baltezza\b|\bis300\b|\bis250\b", 1998),
    # Mazda - RX-8 e MX-5 NC automaticos tem borboleta de fabrica
    (r"\brx-?8\b", 2004),
    (r"\bmx-?5\b", 2006),
]

# ---------------------------------------------------------------------------
# Sinais de "projeto": carro pra montar, nao pra usar de daily.
PROJECT_KW = [
    "projeto", "projeto de drift", "para drift", "pra drift", "drift", "drifit",
    "preparado", "preparada", "preparacao", "turbinado", "turbo forjado",
    "swap", "motor trocado", "coxim", "gaiola", "santantonio", "santo antonio",
    "roll bar", "rollbar", "rebaixado", "suspensao roscada", "coilover",
    "arrancada", "pista", "track day", "trackday", "autocross",
    "reformar", "para reformar", "restaurar", "para restaurar", "restauro",
    "inteiro pra montar", "so montar", "desmontado", "sem motor", "sem cambio",
    "motor fundido", "fundiu", "sem funcionar", "nao esta rodando",
]


def _tem(texto: str, palavras) -> bool:
    return any(re.search(r"\b" + re.escape(p) + r"[a-z]?\b", texto) for p in palavras)


def detectar_paddles(titulo: str, descricao: str | None, gearbox: str | None,
                     ano: int | None) -> str:
    """-> 'sim' | 'provavel' | 'nao'."""
    if gearbox == "manual":
        return "nao"  # manual nao precisa de borboleta

    texto = norm(f"{titulo} {descricao or ''}")
    if _tem(texto, PADDLE_KW):
        return "sim"

    if gearbox == "automatico" and ano:
        t = norm(titulo)
        for padrao, ano_min in MANUAL_MODE_TABLE:
            if re.search(padrao, t) and ano >= ano_min:
                return "provavel"
    return "nao"


def detectar_projeto(titulo: str, descricao: str | None, *, leilao: bool = False,
                     km: int | None = None) -> bool:
    texto = norm(f"{titulo} {descricao or ''}")
    if _tem(texto, PROJECT_KW):
        return True
    # ex-leilao/batido ja e projeto por definicao
    if leilao:
        return True
    # rodado alto costuma ser carro de sacrificar, nao de preservar
    return bool(km and km >= 250000)
