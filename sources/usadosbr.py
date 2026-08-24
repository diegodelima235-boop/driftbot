"""Usados BR - classificados nacionais, mistura loja e particular.

Vale por dois motivos que a OLX nao da:
  1. `comments` = texto livre do vendedor. E ali que aparece "borboleta", "projeto",
     "preparado", "drift". A listagem da OLX so devolve titulo padronizado.
  2. `shifter` = cambio, e `optionals` = lista de opcionais.

Os dados vem do __NEXT_DATA__, dentro da hidratacao do React Query
(`dehydratedState.queries[].state.data.vehicles.data`), 19 por pagina.

Filtro de PRECO na URL nao funciona (?precoAte e ignorado, devolve os mesmos 566);
o de ESTADO funciona (/carros/sp/... cai pra 201). Entao filtramos preco no matcher.
"""
from __future__ import annotations

import json
import logging
import re

from core.models import Listing, parse_gearbox, parse_price, parse_year

log = logging.getLogger("driftbot.usadosbr")

NAME = "usadosbr"
SLUG_QUERIES = True  # recebe "bmw/320i", nao texto livre
BASE = "https://www.usadosbr.com/carros"
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _vehicles(html_text: str) -> list[dict]:
    m = NEXT_RE.search(html_text)
    if not m:
        return []
    try:
        nd = json.loads(m.group(1))
        queries = nd["props"]["pageProps"]["dehydratedState"]["queries"]
    except (KeyError, ValueError):
        return []
    for q in queries:
        if "veiculos" not in str(q.get("queryKey")):
            continue
        data = (q.get("state") or {}).get("data") or {}
        veic = data.get("vehicles") or {}
        if isinstance(veic.get("data"), list):
            return veic["data"]
    return []


def _to_listing(v: dict) -> Listing | None:
    vid = v.get("id")
    if not vid or v.get("status") != "active":
        return None

    version = v.get("version") or {}
    model = version.get("model") or {}
    brand = model.get("brand") or {}
    titulo = " ".join(
        x for x in (brand.get("name"), model.get("name"), version.get("name")) if x
    ).strip()
    if not titulo:
        return None

    ano = v.get("yearMod") or v.get("yearMan")
    adv = v.get("advertiser") or {}
    cidade = ((adv.get("city") or {}).get("name")) or None
    uf = (((adv.get("city") or {}).get("state")) or {}).get("abbrev")

    img = (v.get("image") or {}).get("name")
    if img and not img.startswith("http"):
        img = "https://images.usadosbr.com" + img

    optionals = ", ".join(
        o.get("name", "") for o in (v.get("optionals") or []) if isinstance(o, dict)
    )
    descricao = f"{v.get('comments') or ''} {optionals}".strip()

    slug = v.get("slug") or ""
    url = f"https://www.usadosbr.com/carro/{model.get('slug', '')}/{slug}-{vid}"

    return Listing(
        source=NAME,
        ext_id=str(vid),
        title=f"{titulo} {ano or ''}".strip(),
        url=url,
        price=parse_price(v.get("value")),
        year=parse_year(str(ano)),
        km=parse_price(v.get("km")),
        city=cidade,
        uf=uf,
        image=img,
        gearbox=parse_gearbox((v.get("shifter") or {}).get("name")),
        description=descricao or None,
    )


def search(http, cfg: dict, slug: str, pages: int = 2) -> list[Listing]:
    """`slug` = 'bmw/320i'. Restringe por UF, que e o unico filtro que a URL respeita."""
    uf = (cfg["region"].get("uf") or "").lower()
    out: list[Listing] = []
    vistos: set[str] = set()

    for p in range(1, max(1, pages) + 1):
        url = f"{BASE}/{uf}/{slug.strip('/')}" if uf else f"{BASE}/{slug.strip('/')}"
        if p > 1:
            url += f"?page={p}"
        r = http.get(url)
        if r is None:
            break
        veics = _vehicles(r.text)
        if not veics:
            break
        novos = 0
        for v in veics:
            li = _to_listing(v)
            if li and li.ext_id not in vistos:
                vistos.add(li.ext_id)
                out.append(li)
                novos += 1
        if novos == 0:
            break

    log.info("usadosbr '%s': %d anuncios", slug, len(out))
    return out
