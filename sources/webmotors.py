"""Webmotors - API JSON interna.

IMPORTANTE: o parametro `palavrachave` da API e IGNORADO - buscar "bmw", "opala" ou
"mustang" devolve exatamente o mesmo estoque generico. Entao esta fonte NAO faz busca
por termo: ela varre o estoque inteiro filtrado por preco/ano/UF e deixa o matcher
garimpar os alvos. Por isso e marcada como BROAD (roda 1x por ciclo, nao 1x por termo).
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from core.models import Listing, parse_gearbox, parse_price, parse_year

log = logging.getLogger("driftbot.webmotors")

NAME = "webmotors"
BROAD = True  # ignora o termo de busca; o driver chama uma vez so
MAX_PAGES = 8
PER_PAGE = 48

API = "https://www.webmotors.com.br/api/search/car"


def _dig(d, *path, default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
        if cur is None:
            return default
    return cur


def _page_url(cfg: dict, page: int) -> str:
    inner = (
        "https://www.webmotors.com.br/carros/estoque"
        f"?estadocidade=Sao+Paulo"
        f"&precoate={cfg['budget']['max_price']}"
        f"&anode={cfg['year']['min']}&anoate={cfg['year']['max']}"
    )
    return (
        f"{API}?url={quote(inner, safe='')}"
        f"&actualPage={page}&displayPerPage={PER_PAGE}&order=1"
    )


def _to_listing(it: dict) -> Listing | None:
    spec = it.get("Specification") or {}
    make = _dig(spec, "Make", "Value") or ""
    model = _dig(spec, "Model", "Value") or ""
    version = _dig(spec, "Version", "Value") or ""
    titulo = " ".join(x for x in (make, model, version) if x).strip()
    if not titulo:
        return None

    uid = str(it.get("UniqueId") or it.get("ID") or "")
    if not uid:
        return None

    ano = spec.get("YearModel") or spec.get("YearFabrication")
    seller = it.get("Seller") or {}
    photos = (it.get("Media") or {}).get("Photos") or []
    img = None
    if photos:
        img = photos[0] if isinstance(photos[0], str) else (photos[0] or {}).get("URL")
    if img:
        if img.startswith("//"):
            img = "https:" + img
        img = img.replace("{s}", "1").replace("{w}", "800").replace("{h}", "600")

    cidade = seller.get("City")
    uf = seller.get("State")
    # Webmotors as vezes manda "Sao Paulo (SP)" no State
    if uf and len(uf) > 2:
        import re as _re

        m = _re.search(r"\(([A-Z]{2})\)", uf)
        uf = m.group(1) if m else uf[:2].upper()

    # o Webmotors escreve o cambio no fim da versao:
    # "CHEVROLET OPALA 4.1 DIPLOMATA 12V GASOLINA 4P MANUAL"
    gearbox = parse_gearbox(spec.get("Transmission"), version, titulo)

    return Listing(
        source=NAME,
        ext_id=uid,
        title=f"{titulo} {ano or ''}".strip(),
        url=f"https://www.webmotors.com.br/comprar/{uid}",
        price=parse_price(_dig(it, "Prices", "Price")),
        year=parse_year(str(ano)),
        km=parse_price(spec.get("Odometer")),
        city=cidade,
        uf=uf,
        image=img,
        gearbox=gearbox,
    )


def search(http, cfg: dict, query: str | None = None) -> list[Listing]:
    """Varre o estoque paginado. `query` e ignorado (ver docstring do modulo)."""
    out: list[Listing] = []
    vistos: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        data = http.get_json(_page_url(cfg, page))
        if not data:
            break
        results = data.get("SearchResults") or []
        if not results:
            break
        novos = 0
        for it in results:
            li = _to_listing(it)
            if li and li.ext_id not in vistos:
                vistos.add(li.ext_id)
                out.append(li)
                novos += 1
        if novos == 0:
            break  # a API comecou a repetir - fim util da paginacao

    log.info("webmotors varredura ampla: %d anuncios em ate %d paginas", len(out), MAX_PAGES)
    return out
