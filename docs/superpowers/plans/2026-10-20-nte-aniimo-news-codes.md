# NTE + Aniimo (notícias e códigos) e migração dos códigos do Endfield: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postar no Discord os códigos de NTE, Aniimo e Endfield (via Pocket Tactics e PocketGamer) e as notícias oficiais de NTE e Aniimo.

**Architecture:** Um script novo, `game_codes_scraper.py`, parametrizado por `GAME`, substitui o `arknights_endfield_codes_scraper.py`. Ele tem um parser genérico de "lista com código em negrito", fonte principal e reserva, e state por jogo. As notícias entram como duas fontes novas no `news_scraper.py`, seguindo o padrão `*_process` / `*_build_item` das fontes que já existem. Cada jogo tem seu workflow diário.

**Tech Stack:** Python 3.11, `requests`, `beautifulsoup4`, `pytest`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-20-nte-aniimo-news-codes-design.md`

## Global Constraints

- Nenhuma dependência nova. Usar só o que já está em `requirements.txt` e `requirements-dev.txt`.
- Nunca gravar caminhos absolutos da máquina local em docs, specs ou planos. Usar caminhos relativos ao repositório.
- Commits sem `Co-Authored-By` e sem atribuição ao Claude.
- Speedstorm, MTGA, `scraper.py` e `purge_channels.py` não mudam.
- O código é postado exatamente como aparece na fonte. A comparação com o state usa `normalize_code` (maiúsculas, sem espaços).
- O state do Endfield (`arknights_endfield_codes_state.json`) é preservado. Só podem ser acrescentados campos.
- Notícias em inglês: `/en/` no NTE e `region=en` no Aniimo.
- Os testes rodam offline com fixtures em `tests/fixtures/` (já capturadas em 2026-10-20 e commitadas junto com este plano).

## Review Focus

1. **A página muda e a lista some.** O parser devolve `[]`, o run tenta a reserva, avisa no summary e, se as duas falharem, termina com exit 1. Teste: `test_run_both_sources_fail_exits_1` (Task 2).
2. **O mesmo código com outra capitalização** (`aniimoidyLL` e `aniimoidyll`) não é postado de novo. Teste: `test_run_skips_seen_code_case_insensitive` (Task 2).
3. **A principal cai na primeira execução.** Nada é postado, mesmo que a reserva funcione. Teste: `test_first_run_primary_fail_posts_nothing` (Task 2).
4. **O nome do arquivo de dados do NTE muda** (`newsData<data>.js`). A URL atual é descoberta no `main.html` e, se não houver nenhuma, o run falha. Teste: `test_nte_discover_data_url` (Task 4).
5. **A API do Aniimo responde "Article Query Failed" mesmo quando dá certo.** Usar só `code == 200` e a lista. Lista vazia faz o run falhar. Testes: `test_aniimo_list_ignores_msg` e `test_aniimo_empty_list_raises` (Task 5).

---

### Task 1: Parser de códigos

**Files:**
- Create: `game_codes_scraper.py` (por enquanto, só a parte de parsing)
- Test: `tests/test_game_codes_parser.py`
- Fixtures (já existem): `tests/fixtures/codes/{pockettactics,pocketgamer}_{nte,aniimo,endfield}.html`

**Interfaces:**
- Produces:
  - `parse_codes(html: str) -> List[Dict[str, Optional[str]]]`: itens `{"code": str, "reward": Optional[str]}`, na ordem da página, sem duplicatas (comparação por `normalize_code`).
  - `normalize_code(code: str) -> str`: `re.sub(r"\s+", "", code).upper()`.

Regra do parser. Um protótipo dela, rodado contra as fixtures, produziu exatamente os valores dos testes abaixo:
- Raiz: `soup.find("article") or soup`.
- Percorrer `h2/h3/h4/li` em ordem de documento. Parar no primeiro título cujo texto contenha "expired" (sem diferenciar maiúsculas).
- Ignorar `li` dentro de `<s>`/`<del>` ou que contenha `<s>`/`<del>`.
- O primeiro filho não vazio do `li` precisa ser `<strong>` ou `<b>`. O texto dele, sem `:-–—` no fim, precisa casar com `^[A-Za-z0-9]{4,24}$`. É isso que exclui coisas como "Step 1".
- `reward`: o resto do texto do `li`, sem separadores no início. String vazia vira `None`.

- [ ] **Step 1: Escrever os testes que falham**

```python
def _fx(name): return (FIXTURES / "codes" / f"{name}.html").read_text(encoding="utf-8")

def test_pockettactics_nte():
    codes = [c["code"] for c in parse_codes(_fx("pockettactics_nte"))]
    assert len(codes) == 9
    assert codes[0] == "WONDERLOGGIFT" and "NTEKokushi" in codes

def test_pockettactics_aniimo_preserves_case():
    items = parse_codes(_fx("pockettactics_aniimo"))
    codes = [c["code"] for c in items]
    assert len(codes) == 16
    assert {"aniimazingfroyo", "Aniimo2026", "ANIIMOGIFT"} <= set(codes)
    assert items[0]["reward"] == "20 glimmer (US servers only) (new!)"

def test_pockettactics_endfield():
    assert [c["code"] for c in parse_codes(_fx("pockettactics_endfield"))] == [
        "AKENDFIELDSUB", "ENDFIELDRENEW", "ZAU2SYXHWX5L4ZH", "ENDFIELDGIFT", "ENDFIELD4PC"]

def test_pocketgamer_skips_howto_steps():
    codes = [c["code"] for c in parse_codes(_fx("pocketgamer_aniimo"))]
    assert len(codes) == 14 and not any(c.lower().startswith("step") for c in codes)

def test_stops_at_expired_heading():
    html = "<article><h2>Active</h2><ul><li><strong>NEWONE</strong> - 10 gems</li></ul>" \
           "<h3>Expired codes</h3><ul><li><strong>OLDONE</strong> - 5 gems</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["NEWONE"]

def test_skips_struck_through():
    html = "<article><ul><li><s><strong>DEADCODE</strong> - x</s></li><li><strong>LIVECODE</strong> - y</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["LIVECODE"]

def test_dedupes_case_insensitive():
    html = "<article><ul><li><strong>aniimoidyll</strong> - a</li><li><strong>aniimoidyLL</strong> - a</li></ul></article>"
    assert [c["code"] for c in parse_codes(html)] == ["aniimoidyll"]

def test_no_list_returns_empty():
    assert parse_codes("<html><body>Just a moment...</body></html>") == []
```

- [ ] **Step 2:** `pytest tests/test_game_codes_parser.py -v`. Esperado: FAIL (ImportError).
- [ ] **Step 3:** Implementar `parse_codes` e `normalize_code` em `game_codes_scraper.py`.
- [ ] **Step 4:** `pytest tests/test_game_codes_parser.py -v`. Esperado: 8 passed.
- [ ] **Step 5: Commit**

```bash
git add game_codes_scraper.py tests/test_game_codes_parser.py tests/fixtures/codes
git commit -m "feat(codes): add generic bold-list code parser"
```

---

### Task 2: Execução do scraper de códigos (fontes, state, posts, avisos)

**Files:**
- Modify: `game_codes_scraper.py`
- Test: `tests/test_game_codes_run.py`

**Interfaces:**
- Consumes: `parse_codes`, `normalize_code` (Task 1).
- Produces:
  - `GAMES: Dict[str, Dict]`, com chaves `display`, `primary`, `fallback`, `state_path`, `role_env`:

    | key | display | primary | fallback | state_path | role_env |
    |---|---|---|---|---|---|
    | `nte` | `Neverness to Everness` | `https://www.pockettactics.com/neverness-to-everness/codes` | `https://www.pocketgamer.com/neverness-to-everness/codes/` | `nte_codes_state.json` | `ROLE_ID_NTE` |
    | `aniimo` | `Aniimo` | `https://www.pockettactics.com/aniimo/codes` | `https://www.pocketgamer.com/aniimo/codes/` | `aniimo_codes_state.json` | `ROLE_ID_ANIIMO` |
    | `endfield` | `Arknights: Endfield` | `https://www.pockettactics.com/arknights-endfield/codes` | `https://www.pocketgamer.com/arknights-endfield/codes/` | `arknights_endfield_codes_state.json` | `ROLE_ID_ARKNIGHTS_ENDFIELD` |

  - `fetch_html(url: str) -> str`: lança exceção em status diferente de 2xx. Usar o User-Agent de navegador que o `news_scraper.USER_AGENT` já usa (copiar o valor, não importar).
  - `post_webhook(webhook_url: str, content: str) -> Optional[str]`: copiar do `arknights_endfield_codes_scraper.py` (inclui o tratamento de DRY_RUN).
  - `run(game: str) -> int`: devolve o exit code. `main()` lê `GAME` (obrigatório e precisa estar em `GAMES`), `WEBHOOK_URL_CODEX` (obrigatório) e `WEBHOOK_URL_SUMMARY` (opcional), e chama `sys.exit(run(game))`.
  - O state ganha o campo `last_source` (`"primary" | "fallback" | "none"`). `seen_codes` é gravado normalizado e ordenado.

Comportamento do `run` (spec, seção "Códigos"):
1. `first_run = not state.get("seen_codes")`.
2. Tentar a principal. Ela falha se `fetch_html` lançar exceção ou se `parse_codes` devolver `[]`. Se falhar e for `first_run`, não tentar a reserva: o status vira `"none"`. Fora do first run, tentar a reserva com o mesmo critério de falha.
3. Se o status for diferente de `state.get("last_source", "primary")`, postar no summary: `**<display> codes:** primary source failed (<motivo>); using fallback <url>`, ou `...no source available (<motivos>)`, ou `...primary source is back`.
4. Status `"none"`: gravar o state (com `last_source`) e devolver 1.
5. Postar cada código cujo `normalize_code` não esteja em `seen_codes`, com a mesma formatação e a mesma regra de menção uma vez por run do script antigo: `**<display> — New Code Found!**`, `` `code` ``, `**Rewards:** …` e `<url da fonte usada>`. Só vai para o state o código cujo post deu certo.
6. Health ping semanal sem mudança de comportamento (copiar `should_health_ping` e trocar o nome do jogo na mensagem).
7. Com `DRY_RUN=true`, não gravar o state.

- [ ] **Step 1: Escrever os testes que falham.** Use `monkeypatch` em `fetch_html` (um dicionário url→html, ou exceção) e `post_webhook` (registra `(webhook, content)` e devolve `"id"`), e `tmp_path` para o `state_path` (`monkeypatch.setitem(GAMES["aniimo"], "state_path", tmp_path/"s.json")`).

```python
def test_first_run_posts_all_active_with_single_ping():       # 16 posts no CODEX; só o 1º contém "<@&123>"; exit 0
def test_run_skips_seen_code_case_insensitive():               # state seen_codes=["ANIIMOIDYLL"] -> aniimoidyll não é postado
def test_endfield_existing_state_only_new_code_posted():       # state = conteúdo atual de arknights_endfield_codes_state.json -> só AKENDFIELDSUB
def test_primary_fail_uses_fallback_and_warns_once():          # 1º run: 1 aviso no SUMMARY e last_source="fallback"; 2º run idêntico: nenhum aviso novo
def test_primary_returns_zero_codes_counts_as_failure():       # primary html sem lista -> usa fallback
def test_first_run_primary_fail_posts_nothing():               # state vazio + primary falha -> 0 posts no CODEX, fallback não é buscado, exit 1
def test_run_both_sources_fail_exits_1():                      # seen_codes não vazio, as duas falham -> exit 1, aviso "no source available", last_source="none"
def test_recovery_warns_primary_back():                        # last_source="fallback" e primary ok -> aviso "primary source is back"
def test_failed_post_not_marked_seen():                        # post_webhook devolve None -> código fora de seen_codes
def test_dry_run_does_not_write_state(monkeypatch):            # DRY_RUN=true -> arquivo de state inalterado
```

- [ ] **Step 2:** `pytest tests/test_game_codes_run.py -v`. Esperado: FAIL.
- [ ] **Step 3:** Implementar `GAMES`, `fetch_html`, `post_webhook`, `load_state`, `save_state`, `should_health_ping`, `run`, `main` e o `if __name__ == "__main__"`. Escrever a docstring do módulo no estilo do script antigo, listando as variáveis de ambiente.
- [ ] **Step 4:** `pytest tests/ -v -k "game_codes"`. Esperado: tudo passando.
- [ ] **Step 5:** Smoke test real, sem postar: `GAME=aniimo WEBHOOK_URL_CODEX=https://example.invalid DRY_RUN=true python game_codes_scraper.py`. Esperado: log com cerca de 16 `[DRY_RUN] Would POST` e exit 0.
- [ ] **Step 6: Commit**

```bash
git add game_codes_scraper.py tests/test_game_codes_run.py
git commit -m "feat(codes): add multi-game codes scraper with fallback source"
```

---

### Task 3: Workflows de códigos, migração do Endfield e README

**Files:**
- Create: `.github/workflows/daily-nte-codes.yml`, `.github/workflows/daily-aniimo-codes.yml`
- Modify: `.github/workflows/daily-arknights-endfield.yml`
- Delete: `arknights_endfield_codes_scraper.py`
- Modify: `README.md` (tabela "Supported Games", seção de code scrapers, tabela de Role IDs, nota sobre `WEBHOOK_URL_CODEX`)

**Interfaces:**
- Consumes: CLI `GAME=<key> python game_codes_scraper.py` (Task 2).

- [ ] **Step 1:** No `daily-arknights-endfield.yml`, trocar o passo de execução por `python game_codes_scraper.py` com `GAME: endfield`, mantendo env, state, commit message e horário.
- [ ] **Step 2:** Criar os dois workflows novos copiando o do Endfield:
  - NTE: `name: Daily NTE codes -> Discord`, cron `"45 11 * * *"`, `GAME: nte`, `ROLE_ID_NTE: ${{ secrets.ROLE_ID_NTE }}`, `concurrency.group: nte-codes`, state `nte_codes_state.json`, commit `chore: update NTE codes scrape state`.
  - Aniimo: `name: Daily Aniimo codes -> Discord`, cron `"15 12 * * *"`, `GAME: aniimo`, `ROLE_ID_ANIIMO: ${{ secrets.ROLE_ID_ANIIMO }}`, `concurrency.group: aniimo-codes`, state `aniimo_codes_state.json`, commit `chore: update Aniimo codes scrape state`.
- [ ] **Step 3:** `git rm arknights_endfield_codes_scraper.py`. Depois, `grep -rn "arknights_endfield_codes_scraper" .`. Esperado: nenhuma ocorrência fora de `docs/`.
- [ ] **Step 4:** Atualizar o `README.md`: NTE e Aniimo com Codes = Yes, fontes Pocket Tactics e PocketGamer (incluindo o Endfield) e `ROLE_ID_NTE` / `ROLE_ID_ANIIMO`.
- [ ] **Step 5:** `python -c "import yaml,sys;[yaml.safe_load(open(f)) for f in sys.argv[1:]]" .github/workflows/daily-*-codes.yml .github/workflows/daily-arknights-endfield.yml`. Esperado: nenhum erro.
- [ ] **Step 6: Commit**

```bash
git add .github/workflows README.md
git commit -m "feat(codes): add NTE/Aniimo codes workflows and move Endfield off Game8"
```

---

### Task 4: Fonte de notícias do NTE

**Files:**
- Modify: `news_scraper.py` (constantes, seção nova `# ---------------- NTE (Perfect World) ----------------` depois da Gryphline, e `main()`)
- Test: `tests/test_news_nte.py`
- Fixtures (já existem): `tests/fixtures/news/nte_main.html`, `nte_newsData.js`, `nte_article_264675.html`

**Interfaces:**
- Produces:
  - Constantes: `NTE_GAME = "nte"`, `NTE_BASE = "https://nte.perfectworld.com"`, `NTE_CATEGORY_MAP = {"gamenews": "news", "gamebroad": "notices", "gameevent": "events"}`, `COLOR_BY_GAME["nte"] = 0x8A2BE2`.
  - Constante compartilhada com a Task 5: `NEWS_SKIP_TITLE_RE = re.compile(r"^Account Action Notice|Violation Ban Notice", re.I)`.
  - `nte_discover_data_url(main_html: str) -> str`: `NTE_BASE + "/" + match("include/newsData\d+\.js")`. Sem match, lança `RuntimeError`.
  - `nte_parse_listing(js: str) -> List[Dict]`: só o bloco `"en"`, itens `{"id", "title", "url" (absoluta), "time", "channel"}`, sem duplicatas por `id` (o número no fim da URL). O mesmo item aparece em `news` e na lista do próprio canal.
  - `nte_list() -> List[Dict]`: busca `NTE_BASE + "/en/main.html"`, depois o JS, e chama `nte_parse_listing`. Lista vazia lança `RuntimeError`.
  - `nte_process(state, cutoff_ts) -> Tuple[List[Dict], List[Tuple[str, Dict]]]`: mesmo contrato do `gryphline_process`. Chave `composite_key("perfectworld", "nte", id)`. `effective_ts` é `time` à meia-noite UTC+8. Itens cujo título casa com `NEWS_SKIP_TITLE_RE` ficam de fora tanto de `discovered` quanto de `to_fetch`.
  - `nte_build_item(listing: Dict, article_html: str, effective_ts: int) -> Dict`: `content` é o HTML interno de `div.articleContent`, `author` é `"Neverness to Everness"`, `image` é `None` (o `build_embed` já usa a primeira imagem do corpo) e `category` vem de `NTE_CATEGORY_MAP`.
- `main()`: um bloco igual ao da Gryphline, quando `"nte"` está em `target_games`. `target_games` padrão inclui `"nte"`. Qualquer exceção do `nte_list()` sobe, e o run termina com erro (spec: falha de fonte de notícias).

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_nte_discover_data_url():
    assert nte_discover_data_url(fx("nte_main.html")) == "https://nte.perfectworld.com/include/newsData20260112.js"
    with pytest.raises(RuntimeError): nte_discover_data_url("<html></html>")

def test_nte_parse_listing_dedupes_and_is_english():
    items = nte_parse_listing(fx("nte_newsData.js"))
    assert len(items) == 13 and len({i["id"] for i in items}) == 13
    first = next(i for i in items if i["id"] == "264675")
    assert first["title"] == "Server Maintenance Notice: October 21"
    assert first["url"] == "https://nte.perfectworld.com/en/article/news/gamenews/20261019/264675.html"
    assert first["channel"] == "gamenews"

def test_nte_process_skips_ban_notices(monkeypatch):     # nte_list mockado com o fixture -> nenhum "Account Action Notice" em discovered
def test_nte_build_item():                               # content contém "Hey Appraiser"; category "news"; platform "perfectworld"; published começa com "2026-10-18T16:00:00"
def test_nte_main_first_run_baseline(monkeypatch, tmp_path):  # ONLY_GAME=nte, state vazio -> send_embeds não é chamado e o state ganha chaves "perfectworld:nte:"
```

- [ ] **Step 2:** `pytest tests/test_news_nte.py -v`. Esperado: FAIL.
- [ ] **Step 3:** Implementar as funções e o bloco no `main()`.
- [ ] **Step 4:** `pytest tests/ -v`. Esperado: tudo passando, inclusive os testes que já existiam.
- [ ] **Step 5:** Smoke test real: `ONLY_GAME=nte WEBHOOK_URL_NEWS=https://example.invalid DRY_RUN=true python news_scraper.py`. Esperado: `discovered` > 0 e "First run for nte — baseline only".
- [ ] **Step 6: Commit**

```bash
git add news_scraper.py tests/test_news_nte.py tests/fixtures/news/nte_*
git commit -m "feat(news): add NTE official news source"
```

---

### Task 5: Fonte de notícias do Aniimo

**Files:**
- Modify: `news_scraper.py` (seção nova `# ---------------- Aniimo ----------------` e `main()`)
- Test: `tests/test_news_aniimo.py`
- Fixtures (já existem): `tests/fixtures/news/aniimo_list.json`, `aniimo_detail_100208.json`, `aniimo_detail_100219.json`

**Interfaces:**
- Consumes: `NEWS_SKIP_TITLE_RE` (Task 4).
- Produces:
  - Constantes: `ANIIMO_GAME = "aniimo"`, `ANIIMO_API = "https://worldx-office-api.aniimo.com/api/information"`, `ANIIMO_CATEGORY_MAP = {"new": "news", "announcement": "notices"}`, `COLOR_BY_GAME["aniimo"] = 0x7CCD7C`.
  - `aniimo_parse_list(payload: Dict) -> List[Dict]`: lança `RuntimeError` se `payload.get("code") != 200` ou se `data.list` estiver vazio. **Não** olha `msg`.
  - `aniimo_list() -> List[Dict]`: `GET {ANIIMO_API}/list?region=en&pageNum=1&pageSize=20`.
  - `aniimo_detail(item_id: str) -> Dict`: `GET {ANIIMO_API}/new?id=<id>`, devolve `data`.
  - `aniimo_process(state, cutoff_ts)`: mesmo contrato do `gryphline_process`. Chave `composite_key("aniimo", "aniimo", id)`. `effective_ts` vem de `publishTime` em UTC+8. Filtro `NEWS_SKIP_TITLE_RE`.
  - `aniimo_build_item(listing: Dict, detail: Dict, effective_ts: int) -> Dict`: `url` é `https://aniimo.com/en/newslist/detail/<id>` (verificado: responde 200 com o título da notícia), `content` é `detail.get("content") or ""`, `image` é `mainPic or None`, `author` é `"Aniimo"` e `category` vem de `ANIIMO_CATEGORY_MAP`.
- `main()`: um bloco quando `"aniimo"` está em `target_games`. `target_games` padrão inclui `"aniimo"`. Qualquer exceção sobe.

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_aniimo_list_ignores_msg():
    items = aniimo_parse_list(json.loads(fx("aniimo_list.json")))   # msg == "Article Query Failed" no fixture
    assert len(items) == 20 and items[0]["id"] == "100219"

def test_aniimo_empty_list_raises():
    with pytest.raises(RuntimeError):
        aniimo_parse_list({"code": 200, "msg": "ok", "data": {"list": [], "page_info": {}}})

def test_aniimo_process_skips_ban_notice(monkeypatch):   # "September 16–September 22 Violation Ban Notice" (100175) fora de discovered
def test_aniimo_build_item_with_content():               # 100208: content não vazio, category "notices", url ".../en/newslist/detail/100208"
def test_aniimo_build_item_image_only():                 # 100219: content "" e image == mainPic do fixture
```

- [ ] **Step 2:** `pytest tests/test_news_aniimo.py -v`. Esperado: FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `pytest tests/ -v`. Esperado: tudo passando.
- [ ] **Step 5:** Smoke test real: `ONLY_GAME=aniimo WEBHOOK_URL_NEWS=https://example.invalid DRY_RUN=true python news_scraper.py`. Esperado: `discovered` > 0 e o baseline registrado.
- [ ] **Step 6: Commit**

```bash
git add news_scraper.py tests/test_news_aniimo.py tests/fixtures/news/aniimo_*
git commit -m "feat(news): add Aniimo official news source"
```

---

### Task 6: Workflows de notícias e README

**Files:**
- Create: `.github/workflows/daily-news-nte.yml`, `.github/workflows/daily-news-aniimo.yml`
- Modify: `README.md` (lista "News feeds" e descrição do `WEBHOOK_URL_NEWS`), docstring do topo do `news_scraper.py`

- [ ] **Step 1:** Copiar o `daily-news-zzz.yml` duas vezes:
  - NTE: `ONLY_GAME: nte`, cron `"0 18 * * *"`, `concurrency.group: news-nte`, commit `chore: update news scrape state (nte)`.
  - Aniimo: `ONLY_GAME: aniimo`, cron `"30 18 * * *"`, `concurrency.group: news-aniimo`, commit `chore: update news scrape state (aniimo)`.
- [ ] **Step 2:** Atualizar o README e a docstring: incluir NTE (site oficial da Perfect World) e Aniimo (site oficial).
- [ ] **Step 3:** Validar os YAMLs como no Step 5 da Task 3. Rodar `pytest tests/ -v`. Esperado: tudo passando.
- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily-news-nte.yml .github/workflows/daily-news-aniimo.yml README.md news_scraper.py
git commit -m "feat(news): add NTE/Aniimo news workflows"
```

---

## Depois do merge (manual, com o dono)

1. Criar os secrets `ROLE_ID_NTE` e `ROLE_ID_ANIIMO`.
2. Rodar `workflow_dispatch` com `DRY_RUN=true` em: NTE codes (cerca de 9 novos), Aniimo codes (cerca de 16), Endfield (só `AKENDFIELDSUB`), news NTE e news Aniimo. Conferir os logs.
3. Rodar de verdade. Nas notícias, o primeiro run só registra a base. Nos códigos, o primeiro run posta todos os ativos.
