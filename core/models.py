"""Estrutura unica que toda fonte precisa devolver."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field


def norm(s: str | None) -> str:
    """minusculo, sem acento, espacos colapsados - pra casar palavra-chave."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def parse_price(raw) -> int | None:
    """'R$ 158.000' | 158000 | '38.500,00' -> 158000. None se nao der."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    s = str(raw)
    s = re.sub(r"(?i)\b(r\$|reais)\b", "", s).strip()
    # descarta centavos ",00" e separadores de milhar
    s = re.sub(r",\d{2}$", "", s)
    digits = re.sub(r"[^\d]", "", s)
    if not digits:
        return None
    v = int(digits)
    return v if v > 0 else None


def parse_gearbox(*textos) -> str | None:
    """Normaliza cambio a partir de qualquer texto. -> 'manual' | 'automatico' | None.

    Cuidado com a ordem: 'automatizado' (cambio robotizado, tipo I-Motion) contem
    'automat', e nao e manual de verdade - entra como automatico mesmo.
    """
    for t in textos:
        s = norm(t)
        if not s:
            continue
        if "automat" in s or "cvt" in s or "tiptronic" in s or "dsg" in s:
            return "automatico"
        if "manual" in s or "mecanic" in s:
            return "manual"
    return None


def parse_year(text: str | None) -> int | None:
    """Pega o ano de '... 2P 2003' ou '2003/2004'. Aceita 1960-2030."""
    if not text:
        return None
    anos = [int(y) for y in re.findall(r"\b(19[6-9]\d|20[0-3]\d)\b", str(text))]
    return max(anos) if anos else None


@dataclass
class Listing:
    source: str
    ext_id: str
    title: str
    url: str
    price: int | None = None
    year: int | None = None
    km: int | None = None
    city: str | None = None
    uf: str | None = None
    image: str | None = None
    # "manual" | "automatico" | None (nao informado)
    gearbox: str | None = None
    # texto livre do vendedor, quando a fonte fornece (Usadosbr sim, OLX nao)
    description: str | None = None
    # "sim" (achado no texto) | "provavel" (deduzido do modelo/ano) | "nao"
    paddles: str = "nao"
    is_project: bool = False
    is_auction: bool = False
    is_damaged: bool = False
    matched_target: str = ""
    tier: str = "C"
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        """Chave estavel pra deduplicar entre execucoes."""
        return f"{self.source}:{self.ext_id}"

    @property
    def fingerprint(self) -> str:
        """Detecta o MESMO carro reanunciado com outro id (spam de loja)."""
        base = f"{norm(self.title)}|{self.price}|{norm(self.city)}"
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def price_fmt(self) -> str:
        return f"R$ {self.price:,.0f}".replace(",", ".") if self.price else "a combinar"

    @property
    def local_fmt(self) -> str:
        if self.city and self.uf:
            return f"{self.city}/{self.uf}"
        return self.city or self.uf or "?"
