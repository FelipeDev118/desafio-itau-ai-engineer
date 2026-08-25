# Decisões

Este documento registra trade-offs, limitações e o que faria com mais tempo. Não é um relatório
do que foi feito — isso o código mostra.

## Contexto de tempo

O desafio foi recebido com prazo de entrega no mesmo dia (janela real de poucas horas em vez das
24h nominais, por causa de quando cheguei a ele). Isso definiu a ordem de prioridade: **Nível 1
sólido > Nível 2 sólido > Nível 3 só se sobrasse tempo**, em vez de tentar os três pela metade —
seguindo a orientação explícita do próprio enunciado ("preferimos, com folga, dois níveis
sólidos e bem documentados a três pela metade"). O Nível 3 não foi feito; ver seção própria
abaixo.

## Nível 1 — Tratamento de dados

Três problemas de qualidade encontrados na base de 20 operações:

1. **Duplicata exata** (`OP-0007`, linha idêntica repetida). Tratada com
   `drop_duplicates(subset='id')`, porque um `id` de operação deveria ser único — indica erro
   de extração do sistema legado, não duas operações reais.
2. **Data nula** (`OP-0017`), com `observacao` explícita `"data nao capturada pelo sistema"`.
   Decisão: **não descartar a linha** (valor e cliente são dados válidos), mas **excluí-la do
   cálculo da Regra 1** (que depende de agrupar por data — não é seguro presumir uma data em
   contexto de AML). Ela continua entrando em volume total, contagem por canal e na Regra 2
   (que não depende de data). Alternativa descartada: imputar uma data — rejeitada por criar
   dado artificial num contexto onde isso pode mascarar ou fabricar padrão.
3. **Operação em USD** (`OP-0013`). Convertida para BRL com a taxa fixa do próprio arquivo
   (`valor_brl = valor * taxa_cambio_usd_brl` quando `moeda == 'USD'`), sem consultar cotação
   externa, conforme instruído.

A mesma lógica foi reaproveitada no Nível 2 (`nivel_2/dados.py`) sobre a base de ~320 operações,
onde os mesmos três padrões aparecem em maior quantidade (5 duplicatas, 7 datas nulas, 7
operações em USD) — nenhuma reescrita foi necessária, só encapsular em funções reutilizáveis.

## Nível 1 — Regras determinísticas

- **Regra 1 (fracionamento)**: "ultrapassa R$ 50.000,00" tratado como estritamente `>`;
  "nenhuma atinge R$ 20.000,00" tratado como `< 20000` (ou seja, uma operação de exatamente
  R$ 20.000,00 já conta como "atingiu" e desqualifica o cliente da regra) — usei o limite como
  já "consumido" no ponto exato, para não deixar ambíguo se R$ 20.000,00 exatos contam ou não.
- **Regra 2 (valor atípico)**: aplicada só a clientes com 4+ operações, usando a mediana e o
  limite de 5× sobre `valor_brl` (já convertido), não sobre o valor original — importante,
  porque foi exatamente a conversão de moeda que revelou o outlier em `CLI-A-4` (Nível 1).

## Nível 1 — Validação da Regra 1

Comparei `CLI-A-1` (3 operações no mesmo dia, soma R$ 54.200,00, nenhuma isolada ≥ R$ 20.000,00
→ **deve** sinalizar) com `CLI-A-3` (mesmo padrão superficial — 3 operações no mesmo dia — mas
soma R$ 48.500,00 após remover a duplicata de `OP-0007` → **não deve** sinalizar). O caso
`CLI-A-3` também mostra por que a deduplicação da Parte A.2 importa: sem ela, a soma ficaria em
R$ 65.700,00 e o cliente seria falsamente sinalizado.

## Nível 1 — LLM

- Provedor/modelo: Groq, `openai/gpt-oss-120b` (gratuito, function calling nativo, latência
  baixa). `llama-3.3-70b-versatile`, sugerido no enunciado, não está mais disponível nesta conta
  Groq — os modelos livres mudaram; validei via `client.models.list()` antes de seguir.
- **Achado real da comparação de prompts** (não hipotético — aconteceu na execução salva no
  notebook): o **prompt v1** (genérico, sem contexto de negócio) fez o modelo **alucinar** um
  limite de "R$ 10.000" que não existe em lugar nenhum do enunciado (o limite real é
  R$ 20.000,00) e descrever incorretamente a contagem de operações. O **prompt v2** (papel de
  analista PLD definido, flags determinísticas fornecidas explicitamente como fato, instrução
  de nunca recalcular) citou os números corretos porque eles foram **dados**, não inferidos. A
  lição: o ganho não veio de um prompt "mais bonito", veio de não pedir ao modelo para
  reconstruir contexto numérico sozinho — mesmo princípio de separação regra/LLM, aplicado
  dentro do próprio prompt.
- Resposta malformada: tratada via `re.search` de bloco `{...}` + `json.loads` + validação
  Pydantic (`ParecerLLM`); qualquer falha em qualquer etapa retorna `(None, motivo_do_erro)` em
  vez de lançar exceção.

## Nível 2 — Regras em escala

Nenhuma reescrita foi necessária — `nivel_2/dados.py` reaproveita literalmente as mesmas funções
do Nível 1 (só extraídas do notebook para módulo importável). Isso só foi possível porque desde
o início tratei o Nível 1 com funções puras sobre DataFrame em vez de código solto em células.

**Critério de "sinalização" no ranking dos 10 mais sinalizados**: cada cliente com
`flag_fracionamento=True` conta 1 sinalização; cada **operação individual** com
`flag_valor_atipico=True` conta 1 sinalização (um cliente pode ter várias operações atípicas).
Desempate por `volume_total_brl`. Alternativa descartada: contar fracionamento e valor atípico
igualmente como "1 por cliente" — rejeitada porque um cliente com 3 operações atípicas parece,
na prática, mais preocupante que um com 1 só, e o enunciado não define isso, então documentei a
escolha aqui em vez de arbitrar silenciosamente.

## Nível 2 — Agente e ferramentas

Optei por **function calling nativo** do modelo (via API compatível com OpenAI que o Groq
expõe) em vez de um roteador condicional escrito à mão, porque é o próprio LLM que decide, turno
a turno, quais ferramentas chamar — o enunciado é explícito que "chamar todas sempre não é um
agente, é um script", e queria que a decisão fosse realmente do modelo, não uma condicional
`if flag_fracionamento: chamar(...)` disfarçada de agente.

**O que a execução real mostrou** (conferido em `outputs/pareceres_lote.json`, não presumido):
o agente usou **três padrões distintos** de ferramentas entre os 10 clientes — sempre
`historico_cliente` como base, e depois variando: 6 clientes receberam as três ferramentas,
2 receberam `historico_cliente` + `operacoes_do_dia`, e 2 receberam `historico_cliente` +
`perfil_canal`. Ou seja, ele de fato **não chama tudo sempre** (só 6 de 10 casos usaram as três).

**Porém, a seleção não é bem direcionada — e a causa é um defeito meu de desenho do prompt.**
Eu esperava que clientes com `flag_fracionamento` fossem justamente os que disparariam
`operacoes_do_dia` (a ferramenta de recorte diário). Aconteceu quase o contrário: 7 dos clientes
**sem** fracionamento chamaram `operacoes_do_dia`, e `CLI-029` — que **tem** a flag de
fracionamento, o caso onde olhar o dia é mais justificado — **não chamou**. Investigando, a
causa é clara: o prompt informa ao agente *que* a flag de fracionamento está ativa, mas **não
informa em qual data** o fracionamento ocorreu. Sem a data, o agente não tem o que passar para
`operacoes_do_dia` — as únicas datas visíveis para ele são `data_min`/`data_max` do
`historico_cliente`, que não são as datas relevantes. A ferramenta existe, mas o agente foi
posto numa situação em que não consegue usá-la no caso certo.

**Correção que faria** (não aplicada por falta de tempo hábil antes do prazo): incluir no prompt
as datas específicas que dispararam a Regra 1 (já são calculadas em `flag_fracionamento()`, em
`dados.py` — a coluna `data` do DataFrame de candidatos), transformando a flag de um booleano em
`{"flag_fracionamento": true, "datas": ["2026-03-08"]}`. A validação seria reexecutar o lote e
verificar se os clientes com fracionamento passam a chamar `operacoes_do_dia` **nas datas
sinalizadas** — hoje isso não acontece, e é uma limitação real desta entrega, não um detalhe.

**Bug de API encontrado e contornado**: o modelo `openai/gpt-oss-120b` via Groq eventualmente
tenta "chamar" uma ferramenta fictícia chamada `JSON` para devolver a resposta final (em vez de
simplesmente responder em texto), o que a API rejeita com `400 tool_use_failed`. O conteúdo
gerado, porém, vem embutido no próprio corpo do erro (`failed_generation`). Implementei
`_extrair_parecer_de_erro_tool_json` em `nivel_2/agente.py` para recuperar o parecer desse
campo em vez de deixar a chamada quebrar — isso é tratamento de resposta malformada na prática,
não só na teoria do enunciado.

**Rate limit**: o free tier do Groq tem limite de tokens/minuto (8.000 TPM nesta conta), e o
lote de 10 clientes estourou esse limite na primeira tentativa. Implementei retry com backoff
(`_chat_com_retry`) e espaçamento de 8s entre clientes no `lote.py`. Não implementei cache de
respostas (sugerido no enunciado) por falta de tempo — ver "o que faria com mais tempo".

## Nível 2 — Confronto regra vs. modelo

**Critério de correspondência**: cliente com **ambas** as flags determinísticas ativas
(fracionamento **e** valor atípico) → regra esperaria `nivel_risco = "alto"`; cliente com
**apenas uma** flag ativa → regra esperaria `"medio"`. Escolhido porque reflete a ideia de que
duas regras concordando é mais forte que uma só — e é o critério mais simples e defensável dado
que o enunciado deixa a escolha em aberto.

**Resultado real da execução** (`outputs/confronto_regra_vs_agente.csv`): taxa de concordância
de **40% (4/10)**. Nos 6 casos divergentes, o padrão foi sistemático: o agente **escalou** de
"médio" (esperado pela regra) para "alto", e em todos os casos com justificativa concreta
ancorada nos dados reais — contraparte sem histórico de relacionamento anterior, canal incomum
para o perfil do cliente (ex.: boleto ou espécie onde o padrão é PIX/TED), ou concentração do
volume em poucas operações. Isso é exatamente o comportamento que o enunciado antecipa
("nossas regras são propositalmente simples e vão gerar falsos positivos... um agente que
discorda com boa justificativa pode estar certo"): a Regra 2 (valor atípico) sinaliza qualquer
outlier estatístico do próprio histórico do cliente, sem considerar se a contraparte é conhecida
ou se o canal é coerente com o padrão — informação que o agente tinha acesso via
`historico_cliente` e `perfil_canal`, e a regra determinística, por desenho, não usa. Na leitura
que fiz das 6 divergências, concordo com o agente em pelo menos metade delas — o contexto
adicional (contraparte nova, canal atípico) é um sinal real que a regra simples não captura.

**Achado colateral**: a justificativa do parecer de `CLI-028` veio com caracteres Unicode
invisíveis (zero-width spaces) intercalados no meio do texto, cortando a frase de forma
estranha. O JSON continuou válido e passou na validação de schema — registro isso como
observação de robustez: validar schema não é suficiente para garantir que o *texto* dentro dos
campos está limpo; um pipeline de produção precisaria de uma etapa de sanitização de texto.

## Nível 3

Não implementado, por falta de tempo (prazo bateu no mesmo dia do recebimento do desafio).
**Trilha que escolheria**: B (Servidor MCP local), porque as três ferramentas do Nível 2 já são
funções puras e stateless (`nivel_2/tools.py`) — expô-las via MCP seria principalmente um
wrapper fino sobre o que já existe, sem precisar redesenhar a lógica de negócio, e é a trilha
mais alinhada com "engenharia de agentes" no sentido estrito (separar quem expõe a ferramenta de
quem a consome). **Como atacaria**: usar o SDK oficial de MCP em Python, criar um servidor stdio
que expõe `historico_cliente`, `operacoes_do_dia` e `perfil_canal` como tools MCP com os mesmos
schemas já definidos em `nivel_2/agente.py` (`TOOLS_OPENAI_SCHEMA`, adaptado ao formato MCP), e
trocar a chamada direta em `_executar_tool` por uma chamada MCP via cliente stdio. **Como
validaria**: reexecutar `nivel_2/lote.py` apontando para o cliente MCP em vez do import direto e
comparar se os resultados (parecer, tools chamadas) são idênticos aos já salvos em
`outputs/pareceres_lote.json` — se forem, a troca de transporte não alterou o comportamento do
agente, só a forma de acesso às ferramentas.

## Limitações conhecidas

- **Sem cache de respostas do LLM**: cada execução do lote refaz todas as chamadas, mesmo para
  clientes já processados. Com mais tempo, cachear por `(cliente_id, hash_das_flags)` em disco
  (ex.: SQLite ou arquivo JSON local) evitaria reprocessar em reexecuções e economizaria tokens
  no free tier.
- **Regras determinísticas simples por desenho** (como o próprio enunciado avisa): Regra 1 não
  considera operações em datas adjacentes (ex.: fracionar em 2 dias em vez de 1 escaparia da
  regra); Regra 2 não considera sazonalidade nem crescimento legítimo do negócio do cliente ao
  longo do tempo — um cliente cujo volume médio de operações sobe estruturalmente teria
  operações recentes marcadas como "atípicas" mesmo sem nada suspeito.
- **Function calling depende de um provedor específico** (testado só no Groq/gpt-oss). Trocar de
  provedor pode exigir ajuste no formato do schema de tools ou no tratamento de erros (o bug do
  "tool JSON fictício" é específico deste modelo).
- **Sem teste automatizado** (pytest) para `dados.py`/`tools.py`/`agente.py` — as validações que
  existem são as células de notebook e os `print`/`assert` inline nos scripts, não uma suíte
  formal. Com mais tempo, adicionaria testes unitários para as duas regras determinísticas
  (casos extremos: exatamente R$ 20.000,00, exatamente 3 operações, cliente com exatamente 4
  operações) e um teste de integração do agente com um mock do cliente Groq.
- **Custo/latência não agregados com mais granularidade**: registramos tokens e latência por
  chamada e por cliente, mas não separamos custo do turno de decisão (tool calling) do custo do
  turno de resposta final — útil para otimizar o prompt do turno mais caro.

## O que faria com mais tempo

- Cache de respostas do LLM (chave = hash do prompt + dados de entrada), para não requeimar
  tokens do free tier em reexecuções e permitir rodar o lote sobre os 30 clientes, não só os 10
  mais sinalizados.
- Trilha B do Nível 3 (servidor MCP), como descrito acima.
- Testes unitários para as regras determinísticas e um teste de integração do agente com
  respostas mockadas do LLM (sem depender de rede/rate-limit para rodar o CI).
- Revisar a Regra 1 para considerar janelas de mais de um dia (fracionamento distribuído em
  dias consecutivos), documentando que isso é uma extensão da regra original, não parte do
  enunciado.
