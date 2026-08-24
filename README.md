# DriftBot

Vigia anúncios de carro pra drift e avisa no Telegram quando aparece algo dentro do
orçamento — incluindo batido, sinistrado e ex-leilão que ainda roda.

**Roda sozinho no GitHub Actions, a cada 30 min, 24h por dia**, sem depender do seu PC
estar ligado: <https://github.com/diegodelima235-boop/driftbot/actions>

## Critérios atuais

| | |
|---|---|
| Orçamento | até **R$ 70.000** |
| Tração | **somente traseira** — FWD/quattro/xDrive/4x4 barrados |
| Câmbio | manual, ou automático **com borboleta/Steptronic** |
| Marcas | BMW, Nissan, Toyota/Lexus, Mazda |
| Ano | 1968 a 2014 |
| Região | definida fora do repositório (veja *Onde ficam os segredos*) |

⚠️ Esses critérios são restritivos e a oferta é pequena — o bot passa dias em silêncio,
e isso é o mercado, não bug. Exigir **câmbio manual** sozinho corta ~96% dos anúncios
(quase toda BMW usada aqui é automática); por isso o automático com troca no volante
voltou a ser aceito. Pra abrir o funil, a alavanca mais eficaz é a **região**, não o preço.

## Onde ficam os segredos

Ficam fora daqui **três coisas**: o token do Telegram, o chat_id e a **seção `region`**
(a lista de cidades revelaria onde o dono mora). O `config.yaml` traz só um padrão
genérico pra quem clonar conseguir rodar.

- **Na máquina do dono:** `secrets.local.yaml`, que está no `.gitignore`.
- **No GitHub Actions:** secrets `DRIFTBOT_TG_TOKEN`, `DRIFTBOT_TG_CHAT` e
  `DRIFTBOT_REGION` (esse último é a seção `region` inteira, em JSON).
- Variáveis de ambiente de mesmo nome têm prioridade sobre tudo.

Todo log passa por **redação** antes de ser escrito: a URL da API do Telegram carrega o
token, e num repositório público o artefato `driftbot.log` é baixável por qualquer um —
o GitHub mascara secrets no console, mas **não dentro de artefato**.

---

## 1. Configurar o Telegram (5 minutos, de graça)

1. No Telegram, fale com **@BotFather** → mande `/newbot` → escolha um nome e um
   username terminado em `bot`. Ele responde com um **token** tipo
   `8123456789:AAF...`.
2. Fale com **@userinfobot** → ele responde `Id: 123456789`. Esse é o seu **chat_id**.
3. **Mande qualquer mensagem para o seu bot** (só um "oi"). Sem isso o Telegram
   bloqueia o bot de te escrever primeiro.
4. Abra `config.yaml` e preencha:

```yaml
telegram:
  bot_token: "8123456789:AAF..."
  chat_id: "123456789"
```

## 2. Instalar as dependências

```powershell
python -m pip install --user -r C:\dev\driftbot\requirements.txt
```

## 3. Usar

```powershell
# ver o que ele acharia agora, sem enviar nada (não marca nada como visto)
python C:\dev\driftbot\driftbot.py --dry-run

# checar se as fontes ainda estão funcionando
python C:\dev\driftbot\driftbot.py --test

# uma varredura, envia de verdade (ideal pro Agendador de Tarefas)
python C:\dev\driftbot\driftbot.py --once

# fica rodando, varre a cada 30 min (fecha com Ctrl+C)
python C:\dev\driftbot\driftbot.py

# esquecer tudo que já foi visto e mandar de novo
python C:\dev\driftbot\driftbot.py --once --reset
```

Ou é só dar duplo clique em **`run.bat`**.

## 4. Rodar sozinho

Já está no ar via **GitHub Actions** — não precisa fazer nada. Comandos úteis:

```powershell
$gh = "C:\Users\bedes\dev-tools\gh\bin\gh.exe"
& $gh workflow run driftbot.yml --repo diegodelima235-boop/driftbot   # rodar agora
& $gh run list --repo diegodelima235-boop/driftbot --limit 5          # ultimas execucoes
& $gh run view <ID> --repo diegodelima235-boop/driftbot --log         # log completo
```

**Cota: nenhuma.** O repositório é **público**, e GitHub Actions é ilimitado e gratuito
em repo público. Por isso dá pra rodar a cada 30 min, 24h por dia.

⚠️ **Se algum dia tornar o repositório privado, troque o `cron` antes.** Privado tem
2.000 min/mês; este cron consumiria ~5.800 (48 execuções/dia × 4 min cobrados) e o bot
pararia no meio do mês. O valor seguro pra repo privado é `0 11-23,0-1 * * *`
(1×/hora, 8h–22h BRT, ~1.800 min/mês).

⚠️ **O GitHub desativa workflow agendado após 60 dias sem nenhum commit no repositório.**
Se o bot emudecer de repente, é provavelmente isso — reative em Actions, ou faça um
commit qualquer de vez em quando.

Existe uma tarefa do Windows chamada `DriftBot`, hoje **desativada** de propósito: ela e o
Actions mantêm bancos separados, então rodar as duas geraria alerta em duplicata. Só
reative se desligar o workflow — `schtasks /change /tn "DriftBot" /enable`

---

## Como funciona

| Peça | O quê |
|---|---|
| `config.yaml` | Orçamento, alvos, filtros, palavras proibidas (região vem de secret) |
| `sources/olx.py` | OLX — a fonte principal |
| `sources/webmotors.py` | Webmotors — varredura ampla do estoque |
| `sources/icarros.py` | iCarros — **desligado**, veja abaixo |
| `sources/usadosbr.py` | Usados BR — **desligado**, veja abaixo |
| `core/drift.py` | Detecta borboleta/Steptronic e se o anúncio é projeto |
| `core/matcher.py` | Decide se o anúncio interessa e dá uma nota de 0 a 100 |
| `core/store.py` | SQLite: não repete alerta e avisa quando o preço cai |
| `core/notify.py` | Manda no Telegram, com foto |

### Pontuação
A nota prioriza o que importa pra ir ver o carro: **proximidade** (cidade vizinha vale
mais que a outra ponta do estado), **tier do alvo** (RWD de verdade > plataforma boa >
só se for barato) e **folga no preço**. Só os melhores do ciclo são enviados (`max_alerts_per_scan`).

---

## Coisas descobertas na marra (não desfaça sem ler)

**OLX e Webmotors bloqueiam por fingerprint TLS, não por User-Agent.**
`requests` toma 403 mesmo com headers perfeitos de Chrome. Por isso o projeto usa
`curl_cffi` com `impersonate="chrome"`, que imita o handshake TLS. Não troque por
`requests`.

**Esta máquina tem `SSL_CERT_FILE` e `SSL_CERT_DIR` apontando para pastas do Warface**
(`C:\MY.GAMES\Warface Clutch\...\cacert.pem`), sobra da engenharia reversa do jogo. Isso
sequestra a validação TLS e derruba qualquer ferramenta Python com
*"unable to get local issuer certificate"*. O `core/http.py` neutraliza essas variáveis
no import. **Se outro projeto Python seu estiver com erro de certificado, é isso.**

**A OLX não guarda os anúncios no HTML.** Ela é Next.js App Router: os dados vêm em
pedaços `self.__next_f.push([1,"<json>"])` que precisam ser concatenados. Não existe
`__NEXT_DATA__`. O array fica na chave `"ads"`.

**A OLX ignora palavra extra na busca.** `"opala batido"` devolve exatamente os mesmos
19 anúncios que `"opala"`. Por isso a varredura de leilão busca a palavra **sozinha**
(`batido`, `leilao`, `sinistro`) e deixa o filtro de alvos garimpar — 5 requisições em
vez de 30, e com resultado real.

**O `palavrachave` da API do Webmotors é ignorado.** Buscar "bmw", "opala" ou "mustang"
devolve o mesmo estoque genérico. Por isso o Webmotors é marcado `BROAD = True`: roda
**uma vez** por ciclo varrendo o estoque filtrado por preço/ano, e o matcher garimpa.

**O iCarros está desligado de propósito.** Testado em 2026-08-24: ignora todos os
filtros de preço (`?precoate`, `?prc`, `?pma` devolvem sempre a mesma lista a partir de
R$ 60.000) e não tem carro antigo — `chevrolet/opala` volta zero. É site de seminovo
caro. O módulo está pronto caso um dia passe a aceitar filtro.

**O Usados BR está desligado, apesar do módulo funcionar.** A paginação dele é quebrada:
`?page=1`, `3`, `6`, `9` e `11` devolvem os **mesmos 19 anúncios**. Nenhum parâmetro de
ordenação ou filtro funciona, e o estoque começa em R$ 78.900 / ano 2013. Vale religar se
o teto passar de ~R$ 80.000 — ele é a única fonte que traz `comments`, o texto livre do
vendedor, onde dá pra confirmar borboleta e "projeto".

**Mercado Livre e Copart ficaram de fora.** O ML devolve um shell de JS de 10 KB (a API
pública `api.mercadolibre.com` agora exige OAuth) e o endpoint de busca da Copart Brasil
responde 404.

---

## Quando parar de funcionar

Site muda, é normal. Rode `--test` primeiro: ele mostra fonte por fonte quantos anúncios
vieram e quantos passaram no filtro.

- **`VAZIO` numa fonte** → o site mudou o HTML/API. Veja o parser daquele módulo.
- **HTTP 403 no log** → anti-bot apertou. Aumente `min_delay`/`max_delay` em
  `core/http.py`.
- **Chegou anúncio que não devia** → adicione a palavra em `blocklist` no `config.yaml`.
- **Não chega nada novo** → normal, o banco só avisa uma vez por anúncio. Use `--reset`.

---

## Sobre o preço: um aviso honesto

Os dados reais coletados na sua região, hoje:

- **Nissan 350Z**: o mais barato do Brasil está em **R$ 150.000**. Não existe 350Z
  rodando por R$ 40.000 — nem batido. O bot procura mesmo assim, mas não conte com isso.
- **370Z, Silvia, RX-7, Supra, Skyline**: mesma história ou pior.
- **O que realmente existe a R$ 40.000 e presta pra drift:** Omega 3.8 V6, Opala,
  Caravan, BMW E36 (325i/328i), Chevette, Maverick. Todos apareceram na varredura, e
  vários a menos de 10 km de você.

O `tier: A` do config já reflete isso.

**Se for de leilão/batido, confira sempre:** se o documento está *baixado* (aí não
volta a rodar), se é *média* ou *grande monta*, o número do chassi e se há laudo de
vistoria. Um Omega de R$ 18.000 com documento baixado custa mais caro que um de
R$ 30.000 regularizado.
