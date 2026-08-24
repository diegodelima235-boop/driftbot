"""OLX - a fonte mais importante pra carro barato/batido/ex-leilao.

Como funciona: a OLX e Next.js App Router. Os anuncios NAO estao no HTML cru nem
num __NEXT_DATA__; vem nos chunks de streaming `self.__next_f.push([1,"<json>"])`.
Concatenando os chunks e achando a chave "ads" temos o array completo, com preco,
cidade, UF, imagem e url ja prontos.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from core.models import Listing, norm, parse_gearbox, parse_price, parse_year

log = logging.getLogger("driftbot.olx")

NAME = "olx"
BASE = "https://www.olx.com.br/autos-e-pecas/carros-vans-e-utilitarios"
CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


def _decode_rsc(html_text: str) -> str:
    """Junta os chunks do React Server Components num buffer unico."""
    buf = []
    for raw in CHUNK_RE.findall(html_text):
        try:
            buf.append(json.loads(raw))
        except Exception:
            continue
    return "".join(buf)


def _extract_ads(buf: str) -> list[dict]:
    """Acha `"ads":[...]` e faz parse com contagem de colchetes."""
    key = '"ads":'
    i = buf.find(key)
    while i >= 0:
        start = i + len(key)
        if start < len(buf) and buf[start] == "[":
            depth = 0
            in_str = False
            esc = False
            for j in range(start, len(buf)):
                c = buf[j]
                if esc:
                    esc = False
                    continue
                if c == "\\":
                    esc = True
                    continue
                if c == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(buf[start : j + 1])
                        except Exception as e:
                            log.debug("parse do array ads falhou: %s", e)
                        break
        i = buf.find(key, i + 1)
    return []


def _to_listing(ad: dict) -> Listing | None:
    list_id = ad.get("listId")
    subject = ad.get("subject")
    if not list_id or not subject:
        return None  # placeholders de anuncio/publicidade

    loc = ad.get("locationDetails") or {}
    imgs = ad.get("images") or []
    image = None
    if imgs and isinstance(imgs[0], dict):
        image = imgs[0].get("original")

    # A OLX manda um bloco `properties` estruturado e completo - medido em
    # 2026-08-24: gearbox e has_auction vem preenchidos em 100% dos anuncios.
    # Ler dai e MUITO melhor do que adivinhar pelo titulo.
    props = {
        p.get("name"): p.get("value")
        for p in (ad.get("properties") or [])
        if isinstance(p, dict)
    }

    year = parse_year(props.get("regdate")) or parse_year(subject)
    km = parse_price(props.get("mileage"))
    # o " A " no fim do titulo ("BMW 320I A 2001") tambem quer dizer automatico -
    # fica de reserva caso a OLX pare de mandar o campo
    gearbox = parse_gearbox(props.get("gearbox")) or (
        "automatico" if re.search(r"\b\d{3}i?a\b|\bi a\b", norm(subject)) else None
    )
    leilao = norm(props.get("has_auction")) == "sim"

    return Listing(
        source=NAME,
        ext_id=str(list_id),
        title=subject.strip(),
        url=ad.get("url") or f"https://www.olx.com.br/vi/{list_id}",
        price=parse_price(ad.get("priceValue") or ad.get("price")),
        year=year,
        km=km,
        city=(loc.get("municipality") or "").strip() or None,
        uf=(loc.get("uf") or "").strip() or None,
        image=image,
        gearbox=gearbox,
        is_auction=leilao,
    )


def _page(http, cfg: dict, query: str, page: int) -> list[Listing]:
    region = (cfg["region"].get("olx_region_path") or "").strip("/")
    path = f"{BASE}/{region}" if region else BASE

    params = [f"q={quote(query)}", f"pe={cfg['budget']['max_price']}"]
    if cfg["budget"].get("min_price"):
        params.append(f"ps={cfg['budget']['min_price']}")
    if cfg["year"].get("max"):
        params.append(f"re={cfg['year']['max']}")
    if cfg["year"].get("min"):
        params.append(f"rs={cfg['year']['min']}")
    if page > 1:
        params.append(f"o={page}")

    r = http.get(f"{path}?{'&'.join(params)}")
    if r is None:
        return []

    out = []
    for ad in _extract_ads(_decode_rsc(r.text)):
        li = _to_listing(ad)
        if li:
            out.append(li)
    return out


def search(http, cfg: dict, query: str, pages: int = 1) -> list[Listing]:
    """Busca com preco/ano/regiao aplicados na URL, opcionalmente paginada."""
    out: list[Listing] = []
    vistos: set[str] = set()
    for p in range(1, max(1, pages) + 1):
        got = _page(http, cfg, query, p)
        novos = [x for x in got if x.ext_id not in vistos]
        for x in novos:
            vistos.add(x.ext_id)
        out.extend(novos)
        if len(got) < 40 or not novos:
            break  # fim dos resultados
    log.info("olx '%s': %d anuncios", query, len(out))
    return out
