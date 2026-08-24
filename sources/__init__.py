"""Registro de fontes. Cada modulo expoe NAME e search(http, cfg, query) -> [Listing]."""
from . import icarros, olx, usadosbr, webmotors

REGISTRY = {
    olx.NAME: olx,
    icarros.NAME: icarros,
    webmotors.NAME: webmotors,
    usadosbr.NAME: usadosbr,
}


def get(name: str):
    return REGISTRY.get(name)


def available() -> list[str]:
    return list(REGISTRY)
