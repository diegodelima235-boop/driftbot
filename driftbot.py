#!/usr/bin/env python
"""
DriftBot - vigia anuncios de carro pra drift e avisa no Telegram.

Uso:
    python driftbot.py --test        # so testa as fontes, nao notifica
    python driftbot.py --once        # uma varredura e sai (bom pro Agendador)
    python driftbot.py               # roda em loop no intervalo do config
    python driftbot.py --dry-run     # varre e mostra o que notificaria, sem enviar
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sources  # noqa: E402
from core.http import Http  # noqa: E402
from core.matcher import Matcher  # noqa: E402
from core.models import norm  # noqa: E402
from core.notify import Telegram  # noqa: E402
from core.store import Store  # noqa: E402

LOG_FMT = "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"


class RedigirSegredos(logging.Filter):
    """Ultima linha de defesa: apaga token e chat_id de QUALQUER log.

    Este repositorio e publico, entao o log do Actions e o artefato driftbot.log sao
    baixaveis por qualquer pessoa. O GitHub mascara secrets no console, mas NAO dentro
    de artefato - o arquivo sobe cru. Este filtro roda antes de tudo ser escrito, e
    cobre inclusive caminho de log que eu nao previ (traceback, lib de terceiro).
    """

    def __init__(self, segredos):
        super().__init__()
        # do maior pro menor: evita redigir um pedaco e deixar o resto legivel
        self.segredos = sorted({s for s in segredos if s and len(s) >= 6}, key=len, reverse=True)

    def _limpar(self, texto: str) -> str:
        for s in self.segredos:
            texto = texto.replace(s, "<REDACTED>")
        return texto

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._limpar(record.msg)

        args = record.args
        if isinstance(args, Mapping):
            # `log.info("x: %s", {...})` guarda o dict CRU em record.args, nao numa
            # tupla. Iterar por cima devolveria as chaves e quebraria a formatacao.
            record.args = {
                k: (self._limpar(v) if isinstance(v, str) else v) for k, v in args.items()
            }
        elif args:
            record.args = tuple(
                self._limpar(a) if isinstance(a, str) else a for a in args
            )
        return True


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format=LOG_FMT,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(ROOT / "driftbot.log", encoding="utf-8"),
        ],
    )
    logging.getLogger("driftbot.http").setLevel(logging.INFO)


def ativar_redacao(cfg: dict) -> None:
    """Liga o filtro nos handlers depois que os segredos ja foram carregados."""
    f = RedigirSegredos([
        cfg["telegram"].get("bot_token"),
        str(cfg["telegram"].get("chat_id") or ""),
    ])
    for h in logging.getLogger().handlers:
        h.addFilter(f)


log = logging.getLogger("driftbot")


def load_config() -> dict:
    """config.yaml + segredos por fora.

    O token do Telegram NAO fica no config.yaml, porque esse arquivo vai pro Git.
    Ordem de prioridade (a ultima vence):
      1. config.yaml            - tudo que nao e segredo, versionado
      2. secrets.local.yaml     - segredos da sua maquina, no .gitignore
      3. variaveis de ambiente  - DRIFTBOT_TG_TOKEN / DRIFTBOT_TG_CHAT (usado no CI)
    """
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    local = ROOT / "secrets.local.yaml"
    if local.exists():
        with open(local, encoding="utf-8") as f:
            for secao, vals in (yaml.safe_load(f) or {}).items():
                cfg.setdefault(secao, {}).update(vals or {})

    tok = os.environ.get("DRIFTBOT_TG_TOKEN")
    chat = os.environ.get("DRIFTBOT_TG_CHAT")
    if tok:
        cfg["telegram"]["bot_token"] = tok
    if chat:
        cfg["telegram"]["chat_id"] = chat

    # A regiao (cidades da vizinhanca dele) tambem fica fora do repo publico.
    # Vem como JSON num secret unico, porque e uma secao inteira e nao um valor.
    regiao = os.environ.get("DRIFTBOT_REGION")
    origem = "padrao generico do config.yaml"
    if local.exists() and (yaml.safe_load(open(local, encoding="utf-8")) or {}).get("region"):
        origem = "secrets.local.yaml"
    if regiao:
        try:
            cfg["region"].update(json.loads(regiao))
            origem = "secret DRIFTBOT_REGION"
        except ValueError as e:
            log.error("DRIFTBOT_REGION nao e JSON valido: %s", e)

    # Diagnostico sem vazar nada: diz a ORIGEM e a CONTAGEM, nunca os nomes das
    # cidades - este log e publico. Sem isso nao da pra saber se o secret pegou.
    n = len(cfg["region"].get("cidades_perto") or []) + len(
        cfg["region"].get("cidades_medio") or []
    )
    log.info("regiao carregada de: %s (%d cidades priorizadas)", origem, n)
    if n == 0:
        log.warning("nenhuma cidade priorizada - proximidade nao vai pontuar")

    return cfg


def build_queries(cfg: dict) -> list[tuple[str, str]]:
    """[(fonte, termo)] pras fontes que buscam por termo/slug.

    Fontes marcadas BROAD ficam de fora daqui - o driver chama cada uma UMA vez,
    porque elas ignoram o termo e varrem o estoque inteiro (caso do Webmotors).
    """
    enabled = [s for s in cfg["scan"]["sources"] if s in sources.REGISTRY]
    out: list[tuple[str, str]] = []

    for s in enabled:
        mod = sources.get(s)
        if getattr(mod, "BROAD", False):
            continue
        # Fonte que busca por slug (marca/modelo) le a chave de mesmo nome no alvo:
        # `usadosbr: ["bmw/320i"]`. As demais usam `queries` (texto livre).
        usa_slug = getattr(mod, "SLUG_QUERIES", False)
        for t in cfg["targets"]:
            termos = t.get(s if usa_slug else "queries") or []
            for q in termos:
                out.append((s, q))

    seen, uniq = set(), []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


def broad_sources(cfg: dict) -> list[str]:
    return [
        s for s in cfg["scan"]["sources"]
        if s in sources.REGISTRY and getattr(sources.get(s), "BROAD", False)
    ]


def scan(cfg: dict, http: Http, matcher: Matcher, store: Store, tg: Telegram,
         dry_run: bool = False) -> int:
    queries = build_queries(cfg)
    amplas = broad_sources(cfg)
    log.info("varrendo %d buscas + %d fonte(s) ampla(s)...", len(queries), len(amplas))

    achados: dict[str, object] = {}
    falhas: dict[str, int] = {}

    # Varredura por MARCA na OLX - o grosso da cobertura, e o mais barato.
    # Medido em 2026-08-24: "bmw" 6 paginas acha 97 carros em 10s; as 54 buscas por
    # modelo achavam 78 em 152s. Alvo raro (Silvia, AE86...) nao cai aqui - fica na
    # pagina 12 da marca - e por isso continua tendo `queries` proprias.
    if "olx" in cfg["scan"]["sources"]:
        for marca, pags in (cfg["scan"].get("brand_sweep") or {}).items():
            try:
                for li in sources.get("olx").search(http, cfg, marca, pages=pags):
                    hit = matcher.evaluate(li)
                    if hit and hit.uid not in achados:
                        achados[hit.uid] = hit
            except Exception as e:
                falhas["olx"] = falhas.get("olx", 0) + 1
                log.warning("olx marca '%s' quebrou: %s: %s", marca, type(e).__name__, e)

    # Varredura de leilao/batido na OLX.
    # A OLX ignora palavra extra no termo ("opala batido" devolve o mesmo que "opala"),
    # entao NAO adianta combinar modelo+termo. Buscamos a palavra sozinha - cada uma
    # traz uma pagina cheia de carro avariado de qualquer modelo - e o matcher garimpa
    # os que sao alvo. 5 requisicoes em vez de 30, e com resultado de verdade.
    if "olx" in cfg["scan"]["sources"]:
        for termo in cfg["scan"].get("auction_sweep", []):
            try:
                for li in sources.get("olx").search(http, cfg, termo, pages=2):
                    li.is_auction = True
                    hit = matcher.evaluate(li)
                    if hit and hit.uid not in achados:
                        achados[hit.uid] = hit
            except Exception as e:
                falhas["olx"] = falhas.get("olx", 0) + 1
                log.warning("olx sweep '%s' quebrou: %s: %s", termo, type(e).__name__, e)

    # fontes que ignoram o termo: uma passada so, o matcher garimpa
    for src_name in amplas:
        try:
            for li in sources.get(src_name).search(http, cfg, None):
                hit = matcher.evaluate(li)
                if hit and hit.uid not in achados:
                    achados[hit.uid] = hit
        except Exception as e:
            falhas[src_name] = falhas.get(src_name, 0) + 1
            log.warning("%s (ampla) quebrou: %s: %s", src_name, type(e).__name__, e)

    for src_name, query in queries:
        mod = sources.get(src_name)
        if mod is None:
            continue
        try:
            listings = mod.search(http, cfg, query)
        except Exception as e:
            falhas[src_name] = falhas.get(src_name, 0) + 1
            log.warning("%s '%s' quebrou: %s: %s", src_name, query, type(e).__name__, e)
            continue

        for li in listings:
            hit = matcher.evaluate(li)
            if hit and hit.uid not in achados:
                achados[hit.uid] = hit

    log.info("%d anuncios passaram no filtro", len(achados))
    if falhas:
        log.warning("fontes com erro: %s", falhas)

    # triagem contra o banco: novo? baixou de preco?
    fila: list[tuple] = []
    for li in achados.values():
        notificar, motivo = store.triage(li, cfg["budget"]["price_drop_alert_pct"])
        if notificar:
            fila.append((li, motivo))

    fila.sort(key=lambda x: x[0].score, reverse=True)
    limite = cfg["scan"]["max_alerts_per_scan"]
    restantes = 0
    if len(fila) > limite:
        restantes = len(fila) - limite
        log.info("%d na fila, mandando os %d melhores (limite do config); "
                 "os outros %d vao nos proximos ciclos", len(fila), limite, restantes)
        fila = fila[:limite]

    if not fila:
        log.info("nada novo.")
        return 0

    if dry_run:
        print(f"\n{'=' * 78}\n  {len(fila)} ALERTAS (dry-run, nada enviado)\n{'=' * 78}")
        for li, motivo in fila:
            print(f"\n  [{li.score:3}] {li.tier} {'[LEILAO/BATIDO] ' if li.is_auction else ''}{li.title[:70]}")
            print(f"        {li.price_fmt}  |  {li.local_fmt}  |  {li.year or '?'}  |  {motivo}")
            print(f"        {li.url}")
        return len(fila)

    enviados = tg.send_batch(fila)
    for li in enviados:
        store.mark_notified(li)

    pendentes = len(fila) - len(enviados)
    log.info("%d alertas enviados no Telegram", len(enviados))
    if restantes:
        log.info("%d anuncios ainda na fila - vao no proximo ciclo", restantes)
    if pendentes:
        log.warning("%d falharam no envio - serao tentados de novo", pendentes)
    return len(enviados)


def coletar_tudo(cfg: dict, http: Http, matcher: Matcher) -> list:
    """Varre todas as fontes e devolve TUDO que passou no filtro, ja ordenado."""
    achados: dict[str, object] = {}

    def guardar(listings):
        for li in listings:
            hit = matcher.evaluate(li)
            if hit and hit.uid not in achados:
                achados[hit.uid] = hit

    if "olx" in cfg["scan"]["sources"]:
        olx = sources.get("olx")
        for marca, pags in (cfg["scan"].get("brand_sweep") or {}).items():
            guardar(olx.search(http, cfg, marca, pages=pags))
        for termo in cfg["scan"].get("auction_sweep", []):
            for li in olx.search(http, cfg, termo, pages=2):
                li.is_auction = True
                hit = matcher.evaluate(li)
                if hit and hit.uid not in achados:
                    achados[hit.uid] = hit

    for src, termo in build_queries(cfg):
        guardar(sources.get(src).search(http, cfg, termo))
    for src in broad_sources(cfg):
        guardar(sources.get(src).search(http, cfg, None))

    return sorted(achados.values(), key=lambda x: (x.price or 10**9))


def gerar_lista(cfg: dict, http: Http, matcher: Matcher, tg, enviar: bool) -> int:
    """Inventario completo do que existe agora, do mais barato pro mais caro."""
    itens = coletar_tudo(cfg, http, matcher)
    if not itens:
        print("nada encontrado")
        return 0

    teto = cfg["budget"]["max_price"]
    linhas = [
        f"LISTA COMPLETA - {len(itens)} anuncios ate R$ {teto:,}".replace(",", "."),
        "ordenado do MAIS BARATO pro mais caro",
        "",
    ]
    for i, x in enumerate(itens, 1):
        tags = []
        if x.gearbox == "manual":
            tags.append("MANUAL")
        elif x.paddles == "provavel":
            tags.append("auto/borboleta?")
        elif x.paddles == "sim":
            tags.append("auto+borboleta")
        if x.is_project:
            tags.append("PROJETO")
        if x.is_auction:
            tags.append("LEILAO")
        linhas.append(
            f"{i:3}. {x.price_fmt:>12}  {x.title[:44]:44}  {x.local_fmt[:22]:22}"
            f"  {' '.join(tags)}"
        )
        linhas.append(f"      {x.url}")

    texto = "\n".join(linhas)
    caminho = ROOT / "lista_completa.txt"
    caminho.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"\nsalvo em {caminho}")

    if enviar and tg and tg.enabled:
        # Telegram corta em 4096 chars: manda um resumo e o arquivo completo
        faixas = {"ate 40k": 0, "40-60k": 0, "60-80k": 0, "80-100k": 0}
        for x in itens:
            p = x.price or 0
            if p <= 40000:
                faixas["ate 40k"] += 1
            elif p <= 60000:
                faixas["40-60k"] += 1
            elif p <= 80000:
                faixas["60-80k"] += 1
            else:
                faixas["80-100k"] += 1
        resumo = [f"\U0001f4cb <b>Lista completa: {len(itens)} anuncios</b>", ""]
        resumo += [f"{k}: <b>{v}</b>" for k, v in faixas.items()]
        resumo += ["", "<b>Os 15 mais em conta:</b>"]
        for x in itens[:15]:
            marca = " \U0001f527" if x.is_project else ""
            cambio = " ⚙️MAN" if x.gearbox == "manual" else ""
            resumo.append(
                f'• <a href="{x.url}">{x.price_fmt}</a> — {x.title[:36]} '
                f"({x.local_fmt}){cambio}{marca}"
            )
        tg.send_text("\n".join(resumo)[:4096])
        tg.send_document(caminho, f"Lista completa: {len(itens)} anuncios")
    return len(itens)


def test_sources(cfg: dict, http: Http, matcher: Matcher) -> None:
    """Diagnostico: mostra fonte por fonte se ainda esta funcionando."""
    print(f"\n{'=' * 78}\n  TESTE DE FONTES\n{'=' * 78}")
    # fonte com SLUG_QUERIES espera "marca/modelo", nao texto livre
    amostra = {
        "olx": "bmw 320i",
        "icarros": "bmw/320i",
        "usadosbr": "bmw/320i",
        "webmotors": None,
    }

    for name in cfg["scan"]["sources"]:
        mod = sources.get(name)
        if mod is None:
            print(f"\n  {name:12} SEM MODULO (fontes: {sources.available()})")
            continue
        q = amostra.get(name, "bmw 320i")
        try:
            res = mod.search(http, cfg, q)
            aprovados = [x for x in (matcher.evaluate(r) for r in res) if x]
            status = "OK  " if res else "VAZIO"
            print(f"\n  {name:12} {status} busca '{q}' -> {len(res)} brutos, {len(aprovados)} no filtro")
            for li in aprovados[:4]:
                print(f"      [{li.score:3}] {li.price_fmt:>12}  {li.title[:56]:56}  {li.local_fmt}")
            if res and not aprovados:
                print(f"      (exemplo bruto descartado: {res[0].title[:60]} - {res[0].price_fmt})")
        except Exception as e:
            print(f"\n  {name:12} ERRO  {type(e).__name__}: {e}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="DriftBot - alertas de carro pra drift")
    ap.add_argument("--once", action="store_true", help="uma varredura e sai")
    ap.add_argument("--test", action="store_true", help="testa as fontes, nao notifica")
    ap.add_argument("--dry-run", action="store_true", help="varre e imprime, nao envia")
    ap.add_argument("--lista", action="store_true",
                    help="inventario COMPLETO do que existe agora, do mais barato pro caro")
    ap.add_argument("--reset", action="store_true", help="esquece tudo que ja foi visto")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    cfg = load_config()
    ativar_redacao(cfg)  # antes de qualquer coisa que possa logar segredo

    http = Http()
    matcher = Matcher(cfg)

    if args.test:
        test_sources(cfg, http, matcher)
        return 0

    if args.lista:
        tg = Telegram(cfg["telegram"]["bot_token"], cfg["telegram"]["chat_id"], http)
        gerar_lista(cfg, http, matcher, tg, enviar=not args.dry_run)
        return 0

    db_path = ROOT / "driftbot.db"
    if args.reset and db_path.exists():
        db_path.unlink()
        log.info("banco apagado - tudo volta a ser 'novo'")

    # dry-run usa banco em memoria: assim voce ve o estoque atual inteiro sem
    # marcar os anuncios como vistos (senao o 1o envio real viria vazio)
    store = Store(":memory:" if args.dry_run else db_path)
    tg = Telegram(cfg["telegram"]["bot_token"], cfg["telegram"]["chat_id"], http)

    if not tg.enabled and not args.dry_run:
        log.error("Telegram nao configurado. Preencha bot_token/chat_id no config.yaml "
                  "ou rode com --dry-run.")
        return 1

    try:
        if args.once or args.dry_run:
            scan(cfg, http, matcher, store, tg, dry_run=args.dry_run)
            log.info("banco: %s", store.stats())
            return 0

        intervalo = cfg["scan"]["interval_min"] * 60
        log.info("loop ligado - varrendo a cada %d min. Ctrl+C pra parar.",
                 cfg["scan"]["interval_min"])
        tg.send_text("\U0001f3ce <b>DriftBot ligado</b>\nVou te avisar quando aparecer "
                     "carro de drift na faixa.")
        while True:
            try:
                scan(cfg, http, matcher, store, tg)
            except Exception as e:
                log.exception("varredura falhou: %s", e)
            log.info("dormindo %d min...", cfg["scan"]["interval_min"])
            time.sleep(intervalo)
    except KeyboardInterrupt:
        log.info("encerrado pelo usuario. %s", store.stats())
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
