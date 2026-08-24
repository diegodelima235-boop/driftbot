"""iCarros - HTML renderizado no servidor.

Duas descobertas que definem esse modulo:
1) So a URL no formato /comprar/{marca}/{modelo} devolve resultado. A busca livre
   (?ba=termo) volta vazia. Por isso os alvos trazem slugs no config (chave `icarros`).
2) Cada card carrega um `onclick="pushEventToDataLayer({... select_item_items:{...}})"`
   com item_id, nome, ano, preco, UF e cidade. Ler esse bloco e bem mais estavel do que
   depender das classes CSS, que a iCarros troca com frequencia.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from core.models import Listing, parse_price, parse_year

log = logging.getLogger("driftbot.icarros")

NAME = "icarros"
SLUG_QUERIES = True  # o driver manda slug "marca/modelo", nao texto livre
BASE = "https://www.icarros.com.br/comprar"

ITEM_RE = re.compile(r"select_item_items:\s*\{(.*?)\}", re.S)
FIELD_RE = re.compile(r"(\w+)\s*:\s*'([^']*)'|(\w+)\s*:\s*(\d+)")


def _unescape_js(s: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def _parse_block(block: str) -> dict:
    d = {}
    for m in FIELD_RE.finditer(block):
        k = m.group(1) or m.group(3)
        v = m.group(2) if m.group(2) is not None else m.group(4)
        d[k] = _unescape_js(v)
    return d


def search(http, cfg: dict, slug: str) -> list[Listing]:
    """`slug` = 'bmw/320i', 'nissan/350z', 'chevrolet/opala'..."""
    url = f"{BASE}/{slug.strip('/')}?precoate={cfg['budget']['max_price']}"
    r = http.get(url)
    if r is None:
        return []

    html_text = r.text
    soup = BeautifulSoup(html_text, "lxml")

    hrefs: dict[str, str] = {}
    titulos: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"/d(\d+)\b", a["href"])
        if m:
            oid = m.group(1)
            hrefs.setdefault(oid, "https://www.icarros.com.br" + a["href"])
            if a.get("title"):
                titulos.setdefault(oid, a["title"].strip())

    images: dict[str, str] = {}
    for img in soup.find_all("img", id=True):
        oid = img.get("id", "")
        if oid.isdigit():
            src = img.get("src") or img.get("data-src") or ""
            if src and "sem-foto" not in src:
                images.setdefault(oid, src if src.startswith("http") else "https:" + src)

    out: list[Listing] = []
    vistos: set[str] = set()
    for m in ITEM_RE.finditer(html_text):
        d = _parse_block(m.group(1))
        oid = d.get("item_id")
        if not oid or oid in vistos:
            continue
        vistos.add(oid)

        ano = d.get("item_variant") or ""
        # item_name e generico ("BMW Serie 3"); o title do <a> traz a versao completa
        titulo = titulos.get(oid) or d.get("item_name") or ""
        if not titulo:
            continue
        if ano and ano not in titulo:
            titulo = f"{titulo} {ano}"

        out.append(
            Listing(
                source=NAME,
                ext_id=oid,
                title=titulo.strip(),
                url=hrefs.get(oid) or f"https://www.icarros.com.br/comprar/d{oid}",
                price=parse_price(d.get("price")),
                year=parse_year(ano),
                city=d.get("item_category4"),
                uf=d.get("item_category3"),
                image=images.get(oid),
            )
        )

    log.info("icarros '%s': %d anuncios", slug, len(out))
    return out
