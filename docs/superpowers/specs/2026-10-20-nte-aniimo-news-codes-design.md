# NTE + Aniimo: notícias e códigos, e migração dos códigos do Endfield

Data: 2026-10-20

## Intenção

O bot passa a postar no Discord as notícias e os códigos de resgate de **Neverness to Everness (NTE)** e **Aniimo**, do mesmo jeito que já faz com os outros jogos.

Na pesquisa vimos que o Game8 bloqueia os runners do GitHub Actions: devolve HTTP 202 com corpo vazio, que é a tela de desafio anti-bot. Com isso o scraper de códigos do **Arknights: Endfield** está quebrado em silêncio. O workflow termina em "success", mas o log mostra "Found 0 active codes on page". Os códigos do Endfield passam a vir das mesmas fontes que vamos usar para NTE e Aniimo.

Como deve ficar:
- Quando um código novo de NTE, Aniimo ou Endfield aparece numa fonte confiável, ele é postado no canal de códigos em até um dia, uma única vez, escrito exatamente como precisa ser digitado no jogo.
- As notícias oficiais de NTE e Aniimo aparecem no canal de notícias junto com as de HoYoLAB, Gryphline e Shadowverse.
- Se uma fonte quebrar, alguém fica sabendo pelo canal de resumo. Não queremos repetir a falha silenciosa que aconteceu com o Game8.

## Objetivos

1. Códigos de NTE e Aniimo postados no `WEBHOOK_URL_CODEX`.
2. Códigos do Endfield passam a vir de Pocket Tactics e PocketGamer, saindo do Game8.
3. Notícias oficiais de NTE e Aniimo postadas no `WEBHOOK_URL_NEWS`.
4. Falha de fonte, ou uso da fonte reserva, avisada no `WEBHOOK_URL_SUMMARY`.

## Fora do escopo

- **Eventos e banners do `scraper.py`** (Genshin, HSR, WuWa, Endfield), que também dependem do Game8 e também estão quebrados. Fica para um projeto separado, logo depois deste.
- Scrapers de Speedstorm e MTGA. Eles funcionam e não mudam.
- Purge bot e canais por jogo: NTE e Aniimo não ganham canal próprio de eventos e banners.
- Notícias em outros idiomas além do inglês.

## Anti-objetivos (coisas que fazem o trabalho falhar mesmo que rode)

- Postar código expirado como se fosse novo.
- Mudar a capitalização de um código. Códigos do Aniimo como `Aniimo2026` e `aniimonow` diferenciam maiúsculas de minúsculas.
- Postar de novo os códigos do Endfield que já foram anunciados, só porque a fonte mudou.
- Encher o canal de notícias com avisos semanais de banimento.
- Uma fonte devolver zero códigos e o run terminar em silêncio como "sucesso".

## Fontes (verificadas em 2026-10-20)

### Códigos

Todas as fontes abaixo responderam HTTP 200 a partir de um runner do GitHub Actions, num workflow de teste temporário, tanto com User-Agent de bot quanto de navegador. O Game8 respondeu 202 vazio no mesmo teste.

| Jogo | Principal | Reserva |
|---|---|---|
| NTE | `https://www.pockettactics.com/neverness-to-everness/codes` (9 ativos) | `https://www.pocketgamer.com/neverness-to-everness/codes/` |
| Aniimo | `https://www.pockettactics.com/aniimo/codes` (16 ativos) | `https://www.pocketgamer.com/aniimo/codes/` |
| Endfield | `https://www.pockettactics.com/arknights-endfield/codes` (5) | `https://www.pocketgamer.com/arknights-endfield/codes/` (4) |

Formato observado nos dois sites: dentro de `<article>`, um `<ul>` em que cada `<li>` começa com o código em `<strong>`/`<b>`, seguido de um separador (`-`, `–`, `—` ou `:`) e da recompensa. Exemplo: `<li><strong>aniimonow</strong> - three growth flowers and 10k credits</li>`.

O que é preciso saber:
- Só a seção de ativos conta. No PocketGamer do NTE existe um `<h3>Expired</h3>` depois da lista de ativos. Nada depois de um título com "expired" deve ser lido.
- O Pocket Tactics do NTE tem uma seção "NTE livestream codes". O builder decide se ela entra, depois de conferir se os códigos dela são ativos.
- Algumas listas do PocketGamer misturam códigos antigos do lançamento com os ativos. É mais um motivo para o Pocket Tactics ser a fonte principal.
- O PocketGamer também tem listas com `<strong>` que não são códigos (por exemplo, "Step 1 : Press ESC…" na seção de como resgatar). Usar só a primeira lista de códigos, ou só listas que ficam embaixo de um título com "code" e sem "redeem/how".
- Marcadores do tipo "(new!)" ou "(US servers only)" vêm no texto da recompensa. Manter, porque são informação útil para quem lê.

### Notícias

**NTE**: site oficial da Perfect World. É HTML estático, sem bloqueio.
- Listagem: `https://nte.perfectworld.com/include/newsData20260112.js` (um `var newsdataObj = {...}` com as chaves `cn`, `en` etc.). Cada item tem `title`, `url` (relativa, `/en/article/news/<canal>/<AAAAMMDD>/<id>.html`), `time` (`AAAA-MM-DD`) e `channelName` (`gamenews`, `gamebroad` = Notices, `gameevent` = Events). Em `en` havia 13 itens.
  - Atenção: o nome do arquivo tem data (`20260112`) e pode mudar. Uma alternativa estável é o HTML de `https://nte.perfectworld.com/en/article/news/index.html` e das páginas por canal, mas elas mostram só 3 itens cada. O builder escolhe como descobrir a URL atual. Ela é referenciada em `https://nte.perfectworld.com/en/main.html`.
- Detalhe: o corpo fica em `div.articleContent`, e o título também está em `og:title`. A página não tem imagem de capa útil no `og:image`. Se houver imagem, ela é a primeira `<img>` do corpo.
- ID para o state: o número no fim da URL (`264675`).

**Aniimo**: API JSON pública do site oficial, sem autenticação.
- Listagem: `GET https://worldx-office-api.aniimo.com/api/information/list?region=en&pageNum=1&pageSize=20`, resposta em `data.list[]` com `id`, `title`, `informationType` (`new` ou `announcement`), `mainPic`, `showTime`, `publishTime`. Atenção: o campo `msg` vem "Article Query Failed" mesmo quando dá certo. Use `code == 200` e a lista.
- Detalhe: `GET https://worldx-office-api.aniimo.com/api/information/new?id=<id>` traz `data.content` em HTML. Às vezes vem vazio, por exemplo no id 100219, que é só a imagem de destaque. Nesse caso use `mainPic` e o título.
- Link público: `https://aniimo.com/en/newslist/detail/<id>`. **Ainda não foi verificado**: o builder deve confirmar que o caminho abre a notícia. O sitemap (`https://aniimo.com/api/__sitemap__/urls`) lista o formato `/<lang>/newslist/detail/<id>`, e para inglês provavelmente é `/newslist/detail/<id>` sem prefixo.

Nenhum dos dois sites oficiais publica códigos de resgate. Isso foi conferido em todas as notícias do NTE e nas 20 mais recentes do Aniimo.

## Como fica

### Códigos: um scraper compartilhado

Decisão minha: um script novo, `game_codes_scraper.py`, com o jogo escolhido por variável de ambiente (`GAME=nte|aniimo|endfield`). As configurações por jogo ficam num dicionário no próprio script: nome de exibição, URLs principal e reserva, arquivo de state e variável do cargo. Ele substitui o `arknights_endfield_codes_scraper.py`, que é removido. Speedstorm e MTGA não mudam.

O comportamento segue o do scraper atual do Endfield:
- Um post por código novo no `WEBHOOK_URL_CODEX`. Menção ao cargo só no primeiro post bem-sucedido do run. Mensagem no mesmo formato: título "**<Jogo> — New Code Found!**", código em `` ` ``, recompensa e link da fonte que forneceu o código.
- Health ping semanal no `WEBHOOK_URL_SUMMARY` quando não há código novo.
- `DRY_RUN=true` não posta e não grava nada.
- State por jogo:
  - `arknights_endfield_codes_state.json`: o arquivo existente, mesmo formato, preservando `seen_codes`.
  - `nte_codes_state.json` e `aniimo_codes_state.json`: arquivos novos.
  - Formato: `{"seen_codes": [...], "last_health_ping_iso": ...}`.
- Cargos: `ROLE_ID_NTE` e `ROLE_ID_ANIIMO` (novos) e `ROLE_ID_ARKNIGHTS_ENDFIELD` (o que já existe).

Diferenças em relação a hoje:
- **Capitalização**: o código é postado exatamente como aparece na fonte. Para comparar com o state, use uma versão normalizada (maiúsculas, sem espaços). Assim `aniimoidyll` e `aniimoidyLL` (o Destructoid escreve dos dois jeitos) contam como o mesmo código, e os `seen_codes` do Endfield, que estão todos em maiúsculas, continuam valendo.
- **Reserva**: busca na principal. Se der erro HTTP ou de rede, ou se zero códigos forem extraídos, tenta a reserva e manda um aviso no `WEBHOOK_URL_SUMMARY` dizendo qual fonte falhou e por quê. Se as duas falharem, aviso no summary e o run **termina com erro** (exit ≠ 0), para aparecer vermelho no Actions. Zero códigos conta como falha porque esses jogos sempre têm algum código ativo.
- **Primeira execução (decisão do dono)**: na primeira execução de um jogo (state ausente ou `seen_codes` vazio), todos os códigos ativos encontrados são postados, um post por código, com a menção ao cargo só no primeiro, como em qualquer run. Isso é intencional, para o pessoal resgatar o que já existe. *Decisão minha*: se na primeira execução a principal falhar, o run não posta nada e termina com erro. A reserva (PocketGamer) mistura códigos antigos de lançamento e, sem histórico, eles sairiam como novos. O próximo run tenta de novo.
- **Aviso só na mudança (decisão minha)**: o aviso de "usando reserva" ou de "fonte voltou" vai para o summary apenas quando o status muda, e não todo dia. Para isso o state ganha o campo `last_source` (`"primary"`, `"fallback"` ou `"none"`). Campos novos não quebram o state existente do Endfield.
- **Sem união de fontes**: quando a principal funciona, a reserva não é consultada. *Decisão minha*: a união pegaria mais códigos, mas também traria os códigos antigos que o PocketGamer mistura.

### Notícias: duas fontes novas no `news_scraper.py`

Seguir o padrão de Gryphline e Shadowverse: funções `nte_process` / `nte_build_item` e `aniimo_process` / `aniimo_build_item`, que geram o mesmo dicionário de item (`id`, `platform`, `game`, `url`, `title`, `author`, `content`, `category`, `published`, `updated`, `image`, `summary`, `effective_ts`). Entrada no `COLOR_BY_GAME`. Ativadas no `main()` com `ONLY_GAME=nte` e `ONLY_GAME=aniimo` e também quando roda sem `ONLY_GAME`. Ficam valendo o state compartilhado (`news_state.json`, chave `platform:game:id`), o comportamento de primeira execução por jogo (só registra a base, não posta), o `RUN_LAST_HOURS` e o `DRY_RUN`.

- `platform`: `perfectworld` (NTE) e `aniimo` (Aniimo).
- Categorias: NTE `gamenews` vira `news`, `gamebroad` vira `notices` e `gameevent` vira `events`. Aniimo `new` vira `news` e `announcement` vira `notices`.
- `effective_ts`: NTE usa `time` (só tem a data, então meia-noite UTC+8). Aniimo usa `publishTime`, que é UTC+8 até prova em contrário.
- **Filtro (decisão minha)**: ignorar avisos de banimento recorrentes, ou seja, títulos que começam com "Account Action Notice" (NTE) ou que contêm "Violation Ban Notice" (Aniimo). Eles saem toda semana e não são notícia para o canal.
- **Falha de fonte de notícias (decisão minha)**: se a listagem de NTE ou Aniimo der erro HTTP ou de rede, ou voltar com zero itens, o run **termina com erro** (exit ≠ 0) para aparecer vermelho no Actions. O GitHub já avisa o dono por e-mail quando um workflow falha. Não há post no summary: o `news_scraper.py` hoje não usa esse webhook, e incluí-lo só para essas duas fontes não compensa. Isso vale só para as fontes novas. HoYoLAB, Gryphline e Shadowverse não mudam.
- **Idioma (decisão minha)**: inglês, igual às outras fontes (`DEFAULT_LANGUAGE = "en-us"`, `/en/` no NTE e `region=en` no Aniimo).

### Workflows

Copiar os modelos atuais:
- `daily-nte-codes.yml` e `daily-aniimo-codes.yml`, a partir do `daily-arknights-endfield.yml`: `GAME=…`, secrets `WEBHOOK_URL_CODEX`, `WEBHOOK_URL_SUMMARY` e `ROLE_ID_…`, e commit do state do jogo.
- `daily-arknights-endfield.yml`: passa a rodar `game_codes_scraper.py` com `GAME=endfield`. O state e a mensagem de commit continuam os mesmos.
- `daily-news-nte.yml` e `daily-news-aniimo.yml`, a partir do `daily-news-zzz.yml`.
- Horários: o builder escolhe, espalhados para não coincidirem com os outros jobs que fazem commit no mesmo branch (todos usam `git pull --rebase`, então coincidir também não quebra).

### Documentação

Atualizar o `README.md`: tabela de jogos suportados, lista de feeds de notícias, novos secrets (`ROLE_ID_NTE`, `ROLE_ID_ANIIMO`) e as fontes de código do Endfield.

## Restrições

- Python 3.11, `requests` + `beautifulsoup4` (já estão no `requirements.txt`). Nenhuma dependência nova.
- Tudo roda em `ubuntu-latest` no GitHub Actions. As fontes precisam responder dali, e as listadas acima já foram verificadas.
- Os secrets novos (`ROLE_ID_NTE`, `ROLE_ID_ANIIMO`) precisam ser criados no repositório pelo dono. Sem eles o post sai sem menção, como já acontece hoje.

## O que fica com o builder

- A estrutura interna do `game_codes_scraper.py` e como os parsers do Pocket Tactics e do PocketGamer são organizados (um parser genérico de "lista com código em negrito" provavelmente atende os dois).
- Como descobrir a URL atual do `newsData*.js` do NTE.
- Se a seção "livestream codes" do Pocket Tactics do NTE entra.
- Os horários dos workflows.
- Testes: seguir o estilo de `tests/` (já existe um teste "live" do news scraper). Sugestão: testes do parser de códigos com HTML salvo das páginas reais (fixtures) e casos de "seção Expired ignorada", "capitalização preservada" e "código já visto em outra capitalização não é repostado".

## Validação depois do merge

1. Rodar cada workflow novo pela primeira vez via `workflow_dispatch` com `DRY_RUN=true` e conferir no log os códigos e as notícias encontradas.
2. Para NTE e Aniimo, o dry run da primeira execução deve listar como novos todos os ativos (hoje são cerca de 9 e 16). Conferir que nenhum expirado entrou na lista.
3. Para o Endfield, conferir no dry run que só aparece como novo o que realmente não está no `seen_codes`. Hoje deve ser apenas `AKENDFIELDSUB`.
