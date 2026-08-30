# Decisões

O enunciado pede três coisas aqui — trade-offs, limitações e o que eu faria com mais tempo — e
avisa que não quer relatório do que foi feito. O documento está organizado nessas três seções.
O *o quê* está no código e nas saídas em `outputs/`; aqui está o *por quê*.

---

# 1. Trade-offs

## Provedor e modelo: Groq / `openai/gpt-oss-120b`

**Contra**: Google AI Studio (Gemini) e Ollama local.

Descartei **Ollama** primeiro: rodar local elimina rate limit e questão de dados, mas os modelos
que cabem numa máquina comum têm *function calling* frágil, e o Nível 2 depende inteiramente
disso. Entre **Gemini** e **Groq**, escolhi Groq por latência (o lote de 10 clientes leva ~80s) e
por expor uma API compatível com OpenAI, o que manteria o custo de troca de provedor baixo.

O preço dessa escolha apareceu: o free tier tem **8.000 tokens/minuto**, e o lote estourou o
limite na primeira execução. Foi preciso implementar retry com backoff e espaçar as chamadas em
8s. Com Gemini o limite é por requisição/minuto e não por token, o que teria sido mais folgado
para este volume — em retrospecto, para um lote de 10 clientes com contexto grande, Gemini era a
escolha melhor. Mantive Groq porque a troca no meio do caminho custaria mais que o retry.

Nota: `llama-3.3-70b-versatile`, sugerido no enunciado, **não existe mais** nesta conta Groq.
Descobri validando com `client.models.list()` antes de escrever o resto do código, e não
confiando no nome. Por isso `requirements.txt` fixa versões: a API do pacote `mcp` também mudou
entre 1.x e 2.x durante o desenvolvimento.

## Agente na mão, sem framework

**Contra**: LangChain, LangGraph e PydanticAI, todos citados como opção no enunciado.

Escrevi o loop de *function calling* diretamente sobre o SDK. O argumento a favor do framework é
real: LangGraph me daria estado, retry e observabilidade de graça. Recusei por dois motivos.

O primeiro é que o enunciado diz que a entrevista vai cobrar que eu explique as decisões do meu
próprio código — e um `AgentExecutor` esconde exatamente a parte que está sendo avaliada (como o
modelo decide, o que volta no `tool_calls`, o que acontece quando a resposta não presta). O
segundo é que o loop tem ~40 linhas; o framework seria mais código de configuração que de
lógica.

Isso se pagou na prática. Dois bugs reais só foram tratáveis porque eu tinha o loop na mão:

- O modelo às vezes tenta chamar uma ferramenta **fictícia chamada `JSON`** para devolver a
  resposta final, e a API rejeita com `400 tool_use_failed`. O conteúdo válido vem dentro do
  corpo do erro, em `failed_generation` — recupero de lá
  (`_extrair_parecer_de_erro_tool_json`). Dentro de um framework, isso teria estourado como
  exceção opaca.
- O retry de rate limit precisou ler o `retry-after` da resposta.

**O que perdi**: não tenho tracing estruturado nem retry configurável por política — reimplementei
à mão versões simplórias das duas coisas.

## Data nula: excluir da Regra 1, manter no resto

**Contra**: descartar a linha inteira, ou imputar uma data.

7 operações no Nível 2 (1 no Nível 1) têm `data: null`, com `observacao` dizendo *"data nao
capturada pelo sistema"* — o próprio dado admite falha de captura.

**Imputar** (média do cliente, data vizinha) foi rejeitado por ser perigoso no domínio: a Regra 1
agrupa por data, então inventar uma data pode **fabricar** um fracionamento que não existiu ou
**mascarar** um real. Em PLD, errar para o lado de "inventei um padrão" é pior que perder um caso.

**Descartar a linha** foi rejeitado porque `valor`, `cliente_id` e `canal` são válidos — jogar
fora distorceria volume total e perfil de canal sem necessidade.

A escolha foi separar por regra: a operação sai só do cálculo que **depende de data** (Regra 1) e
permanece em tudo que não depende (volume, canal, Regra 2). O custo é que a Regra 1 tem um ponto
cego declarado — se o fracionamento real aconteceu justamente nessas operações, não é detectado.

## Deduplicação por `id`

Assumi que `id` de operação é único e que repetição é erro de extração do legado. É a hipótese
mais provável (as linhas são **idênticas** em todos os campos), e a validação da Regra 1 mostra
por que importa: sem deduplicar, `CLI-A-3` somaria R$ 65.700,00 em vez de R$ 48.500,00 e seria
**falsamente sinalizado**. A hipótese contrária — dois eventos reais com mesmo id — implicaria um
sistema de origem tão quebrado que a análise inteira seria inconfiável. Ver limitação
correspondente na seção 2.

## Limite da Regra 1: `< 20.000` para "não atingir"

O enunciado diz "nenhuma operação isolada **atinge** R$ 20.000,00". Tratei R$ 20.000,00 exatos
como **já atingido** (desqualifica), e "ultrapassa R$ 50.000,00" como estritamente `>`. É a
leitura literal de "atingir"; a alternativa (`<= 20.000` ainda passa) alargaria a regra sem base
no texto. Nenhuma operação da base cai exatamente no limite, então a escolha não muda o resultado
aqui — mas mudaria com dados reais, e por isso está registrada.

## Ranking: contar operações atípicas, não regras acionadas

**Contra**: cada regra conta 1 por cliente, independentemente de quantas operações disparou.

Adotei: fracionamento conta 1 (é um padrão do cliente), e **cada operação** atípica conta 1.
Um cliente com 3 operações atípicas é materialmente mais preocupante que um com 1 — tratar os
dois como "1 sinalização" descartaria informação que a regra já produziu. O enunciado não define
isso, então documento em vez de arbitrar em silêncio.

## Critério do confronto: intensidade, não coincidência de regras

**Contra**: "ambas as flags ativas → alto" (foi meu primeiro critério, e eu o descartei).

Ao auditar, percebi que o primeiro critério é **degenerado nesta base**: nenhum dos 10 clientes
dispara as duas regras ao mesmo tempo, então o ramo "alto" nunca seria exercido e os 10 casos
esperariam "médio". A taxa de concordância mediria apenas "com que frequência o agente diz
médio". Registro o descarte porque o raciocínio vale mais que o número.

Critério final:

| Condição determinística | esperado |
|---|---|
| `flag_fracionamento` ativa **ou** 2+ operações atípicas | `alto` |
| exatamente 1 operação atípica, sem fracionamento | `médio` |

Fracionamento vai direto para "alto" porque é padrão **intencional** (structuring), não outlier
estatístico. Resultado, na última execução: **30% de concordância (3/10)**, 7 divergências.

### O que a auditoria dos textos mostrou, e o que ela não mostrou

A tentação é ler "30% de concordância" como "o agente discorda muito da regra, e às vezes está
certo". Conferindo justificativa por justificativa contra os dados reais, a história é mais
específica: em **6 dos 7** divergentes, o agente **identifica corretamente** o mesmo padrão que
disparou a regra e ainda assim classifica abaixo.

- **`CLI-029`** (regra: alto · agente: médio): descreve com precisão verificável — conferi contra
  a base — "4 operações de alto valor (entre R$ 14.326,29 e R$ 19.418,96)... todas via TED ou
  PIX" no dia 26/05/2026, e nomeia o padrão: *"o padrão de fracionamento está presente"*. Mesmo
  assim, médio.
- **`CLI-017`** (regra: alto · agente: médio): reconhece explicitamente *"a flag de fracionamento
  já está ativada"*. Mesmo assim, médio.
- **`CLI-005`**, **`CLI-001`**, **`CLI-028`** (regra: alto · agente: médio): nos três, a operação
  e o canal citados batem com os dados — no caso de `CLI-005`, "espécie (R$ 15,0 mil em 2
  operações)" e "cartão (R$ 31,1 mil em 2 operações)" conferem exatamente com o recálculo
  (R$ 15.013,22 e R$ 31.153,13). Mesmo assim, médio.

**O padrão real não é o agente errando o fato — é o agente tendo um limiar mais alto para "alto"
do que o meu critério.** Ele parece reservar "alto" para quando múltiplos fatores se reforçam
(fracionamento *e* canal atípico, por exemplo), enquanto meu critério dispara com um único sinal.
Nenhum dos dois é "a verdade"; são dois desenhos de threshold diferentes, e a divergência
sistemática — não aleatória — é o dado interessante.

- Na direção oposta, **`CLI-030`** (regra: médio · agente: alto) segue sendo escalada
  **defensável**: R$ 85.546,51 de R$ 117.780,89 concentrados em duas TEDs, concentração que a
  Regra 2, comparando operação a operação contra a mediana, não enxerga isolada.
- **`CLI-013`** não produziu parecer — esgotou os 4 turnos de function-calling sem responder em
  texto. É o tratamento de malformado funcionando como desenhado (`parecer: None` +
  `erro_parsing` explícito, em vez de o processo quebrar), mas expõe um limite real: um caso que
  exige mais idas e vindas pode nunca fechar dentro de `max_turnos`.

**Nota de não-determinismo, que é o achado mais importante desta seção.** A execução anterior
deste confronto (preservada em commit anterior) tinha dado **50% de concordância**, e o parecer
de `CLI-005` **citava uma operação errada** (R$ 409,16, valor abaixo da mediana do cliente — não
é a operação atípica). Nesta execução, com o **mesmo código e os mesmos dados**, `CLI-005` cita
as operações corretas e a concordância caiu para 30%. Isso não invalida a leitura acima — ela é
sobre a execução atual — mas é evidência direta, não só teórica, do problema de reprodutibilidade
tratado em [`ARQUITETURA.md`](ARQUITETURA.md): a mesma pergunta, feita duas vezes, rendeu um
parecer factualmente errado numa vez e correto na outra. Isso é mais sério que qualquer
divergência regra-vs-agente isolada: significa que **este próprio documento estaria diferente**
se eu tivesse rodado o lote uma terceira vez, e é por isso que a seção 3 propõe tratar o parecer
como artefato versionado por hash, não como algo recalculável sob demanda.

## Nível 3: Trilha B (MCP)

**Contra**: Trilha A (multiagente) e Trilha C (interface conversacional).

Escolhi B porque as três ferramentas já eram funções puras e sem estado — expô-las via MCP é
troca de *transporte*, não redesenho de lógica, e cabia no tempo restante. A Trilha A exigiria
inventar critérios de parada e estado compartilhado (mais superfície para fazer mal-feito), e a
C entrega principalmente UI, que não é onde este desafio está sendo avaliado.

Detalhes de arquitetura e conexão em [`ARQUITETURA.md`](ARQUITETURA.md). O trade-off relevante:
**abri mão de simplicidade** (dois processos, protocolo no meio, latência de IPC) em troca de uma
fronteira real entre quem expõe a ferramenta e quem a consome, e de descoberta em runtime — o
agente não tem mais a lista de ferramentas hardcoded.

## Nível 1 em funções puras, não em células soltas

Escrever o Nível 1 como funções sobre DataFrame custou mais que empilhar código em células. Pagou
no Nível 2: `nivel_2/dados.py` reaproveitou a limpeza e as duas regras **sem reescrita** — só
extraiu do notebook para módulo importável. Era a pergunta que o enunciado faz na Parte A do
Nível 2, e a resposta é que não mudaria nada nessa decisão.

---

# 2. Limitações — onde isso quebra com dados reais

## Enviar dados de cliente para uma API externa não sobreviveria a um banco

É a limitação mais séria e não é técnica. Os dados aqui são sintéticos e anônimos
(`CLI-014`, "Alfa Comercio LTDA"). Numa base real haveria nome, CPF/CNPJ e contrapartes
identificáveis, e o pipeline **envia esse conteúdo para a API de um terceiro** a cada parecer.
Isso não passa por LGPD nem por política de segurança da informação de uma instituição
financeira. A arquitetura teria que mudar: modelo hospedado no perímetro do banco, ou
pseudonimização antes do envio com re-identificação só do lado de cá. Nada nesta entrega trata
disso.

## As ferramentas releem a base inteira a cada chamada

`tools.py` chama `carregar_e_limpar()` **e** `aplicar_regras()` a cada invocação — ou seja, lê o
JSON inteiro e recalcula todas as regras de todos os clientes para responder sobre **um**. No
lote foram **26 chamadas de ferramenta = 26 releituras completas**. Com 322 operações são 35 ms
e ninguém percebe; com o volume real de um banco isso não roda. A correção não é micro-otimização
e sim mudar a fronteira: as ferramentas deveriam consultar um *store* já materializado
(banco de dados com índice por `cliente_id` e por data), não reprocessar o arquivo bruto.

## O LLM não é determinístico — mitigado com cache, não resolvido na raiz

A comparação entre transportes (Nível 3) expôs isto sem que eu procurasse: **o mesmo agente, com
as mesmas ferramentas e os mesmos dados, atribuiu `nivel_risco` diferente para 4 de 10 clientes
entre duas execuções**. `CLI-014` saiu `alto` numa e `médio` na outra, com `temperature=0.2`. O
problema voltou a aparecer depois: rodar o lote de novo só para instrumentar custo
(`nivel_2/observabilidade.py`) regenerou os pareceres e a concordância regra-vs-agente caiu de
50% para 30% — mesmo código, mesmos dados.

**O que implementei** (`nivel_2/cache_parecer.py`): parar de tratar "gerar parecer" como função
que sempre recalcula, e tratar como mapa `hash_da_entrada -> parecer`. O hash cobre
`cliente_id` + flags determinísticas + um snapshot de `historico_cliente()` (não a base
inteira) + o modelo + uma versão do prompt (bump manual quando `SYSTEM_PROMPT` muda de
verdade). Entrada igual → parecer reaproveitado, não regerado.

**Prova, não afirmação**: rodei `lote.py` três vezes seguidas. A primeira levou 3min47 (10
chamadas reais + espaçamento de rate limit); a segunda e a terceira levaram **1,3 segundo cada,
com 0 chamadas de API**. `confronto.py` rodado duas vezes em seguida deu **exatamente o mesmo
40% (4/10)** nas duas — antes, cada execução podia dar um número diferente.

**O que isso não resolve, para ser honesto sobre o limite da correção**: o cache garante que o
*mesmo caso* não seja recalculado, então a classificação de um cliente já processado fica
estável entre reexecuções do pipeline. Ele **não** torna o LLM determinístico em si — se eu
apagar o cache e rodar de novo, o novo parecer pode diferir do anterior, porque a chamada
individual ao modelo continua não-determinística. A correção completa (persistir o parecer como
registro histórico datado e imutável, nunca recalculável, mesmo limpando cache) é mais estrutural
e não coube no tempo — ver seção 3.

## Pressupostos das regras que dados reais violam

- **Regra 1 olha um único dia.** Fracionar em dois dias consecutivos escapa inteiramente. É a
  evasão mais óbvia contra essa regra, e ela é trivial de executar.
- **Regra 2 usa a mediana do próprio cliente**, o que a torna instável para clientes com poucas
  operações (o mínimo aqui são 4) e cega para crescimento legítimo do negócio: um cliente cujo
  faturamento sobe estruturalmente passa a ter operações marcadas como atípicas sem nada de
  errado. Também não há noção de sazonalidade.
- **Nenhuma das duas olha a rede**: contraparte compartilhada entre clientes, ciclos de
  ida-e-volta, cadeias de transferência. É onde estão as tipologias que importam de verdade, e é
  fora do alcance de regra por cliente isolado.
- **`id` único** é hipótese, não garantia. Sistemas legados reais reciclam identificadores; se
  isso acontecer, minha deduplicação **apaga operações legítimas** silenciosamente. Com dados
  reais eu deduplicaria por chave composta (`id` + `data` + `valor` + `contraparte`) e emitiria
  alerta em vez de remover em silêncio.
- **Câmbio fixo**, conforme instruído. Real exigiria taxa da data de cada operação — uma remessa
  de 2026-03 e outra de 2026-05 convertidas pela mesma taxa distorcem comparação de valores. Sem
  isso, a Regra 2 pode marcar como atípica uma operação que só parece grande pelo câmbio.

## Validar schema não garante conteúdo íntegro

O parecer de `CLI-028` veio com 4 caracteres Unicode invisíveis (U+200B) no meio da
justificativa, truncando a frase visualmente. O JSON era válido e passou pelo Pydantic. Validação
estrutural não substitui sanitização de texto.

## Outras

- **Sem testes automatizados.** O que existe são `assert`s no notebook e inspeção manual — não
  uma suíte. Para código que decide encaminhar cliente a análise humana, isso é pouco.
- **Acoplado a um provedor.** O tratamento do bug da "tool `JSON`" é específico do
  `gpt-oss` via Groq; trocar de provedor exige revisitar essa parte.
- **Só os 10 mais sinalizados passam pelo agente**, por causa do rate limit; os outros 20
  clientes não recebem parecer, e não há como saber se algum deveria ter sido pego.
- **`max_turnos=4` pode não bastar.** `CLI-013` esgotou o limite sem responder — o tratamento de
  malformado funcionou, mas o caso não chegou a parecer nenhum.

---

# 3. O que faria com mais tempo

## Verificação de aderência do parecer aos dados (prioridade 1)

Numa execução anterior, o parecer de `CLI-005` citou uma operação (R$ 409,16) que não é a
sinalizada pela Regra 2 — bem escrito, plausível, e errado. É o problema mais grave encontrado
nesta entrega, porque passa despercebido por revisão humana, e a não-reprodutibilidade descrita
acima (a mesma pergunta rendendo parecer certo numa vez e errado noutra) o torna imprevisível.

**Arquitetura**: uma etapa de *grounding check* determinística entre o agente e a gravação do
parecer. Ela extrai da `justificativa` toda referência verificável (IDs de operação, valores em
R$, datas) e confere contra as operações reais daquele cliente; o parecer só é aceito se as
referências existirem, e é marcado como `nao_fundamentado` caso contrário.

**Ferramenta**: regex para extrair valores/datas/IDs + comparação contra o DataFrame já em
memória. Deliberadamente **sem LLM** — usar um modelo para auditar outro reintroduz o problema.

**Como validaria**: `CLI-005` é o caso de teste pronto. A verificação tem que reprovar o parecer
que cita R$ 409,16 (valor existe, mas não está entre as operações sinalizadas) e aprovar o de
`CLI-030`, que cita R$ 85.546,51 corretamente. Mediria falsos positivos rodando sobre os 10
pareceres e conferindo à mão.

## Reprodutibilidade do parecer — implementado parcialmente, o resto documentado aqui

O cache por hash (`nivel_2/cache_parecer.py`, seção 1) resolve a reprodutibilidade **entre
reexecuções do pipeline** — validado: `confronto.py` deu 40% duas vezes seguidas, contra números
diferentes a cada rodada antes disso.

O que falta é mais estrutural: o cache de hoje é um arquivo JSON local, que não sobrevive a
trocar de máquina nem tem noção de "versão" além do bump manual em `VERSAO_PROMPT`. A correção
completa trataria o parecer como **registro histórico**, não como cache:

**Arquitetura**: em vez de `hash -> parecer` sobrescrevível, um log append-only —
`(hash_entrada, parecer, timestamp, versao_prompt, modelo)` — onde o mesmo hash pode ter múltiplas
entradas ao longo do tempo (nunca se sobrescreve), e "o parecer atual" é sempre a última entrada
para aquele hash. Isso responde "por que este cliente foi classificado como alto risco em
15/03" de forma auditável, mesmo que o parecer tenha mudado depois.

**Ferramenta**: SQLite em vez de JSON — já dá o append-only e a consulta por timestamp de graça,
e escala melhor que reescrever o arquivo inteiro a cada `salvar()` (o que o `CacheParecer` atual
faz, aceitável para 30 clientes, não para volume real de um banco).

**Como validaria**: alterar um único valor da base de um cliente já cacheado, rodar de novo, e
confirmar duas coisas — o hash muda (então o parecer antigo não é reaproveitado por engano) e o
registro antigo continua consultável por quem precisar da decisão histórica.

## Corrigir o prompt do agente para informar a data do fracionamento

Auditando as saídas descobri que **o agente não usa `operacoes_do_dia` nos casos de
fracionamento**, que é justamente onde ela serve: 7 clientes *sem* a flag chamaram a ferramenta e
`CLI-029`, que *tem* a flag, não chamou. A causa é um defeito meu de desenho — o prompt informa
*que* a flag está ativa, mas **não em qual data**, e sem data não há o que passar para a
ferramenta.

**Arquitetura**: trocar o booleano por `{"flag_fracionamento": true, "datas": ["2026-03-08"]}`. As
datas já são calculadas em `flag_fracionamento()` (`dados.py`), só não são propagadas.

**Como validaria**: reexecutar o lote e exigir que **todo** cliente com fracionamento chame
`operacoes_do_dia` em pelo menos uma das datas sinalizadas — hoje isso é 0%. É verificável
direto em `outputs/pareceres_lote.json`, sem julgamento subjetivo.

## Testes automatizados das regras

**Arquitetura**: `pytest` sobre os limites, que é onde regra de negócio quebra — operação de
exatamente R$ 20.000,00, soma de exatamente R$ 50.000,00, cliente com exatamente 4 operações,
cliente com data nula em todas. Mais um teste do agente com o cliente Groq mockado, para rodar
sem rede nem rate limit.

**Como validaria**: os casos-limite são construídos à mão com resultado esperado conhecido — o
teste falha se alguém mudar `>` para `>=`. Hoje nada me protege disso.

## Regra 1 com janela deslizante

**Arquitetura**: janela de 3 dias corridos em vez de dia calendário, via `rolling` sobre a série
diária por cliente. Fica explícito que é **extensão** da regra do enunciado, não a regra pedida —
por isso conviveria com a original em vez de substituí-la, para não invalidar a comparação.

**Como validaria**: a regra estendida tem que capturar tudo que a original captura (superconjunto
verificável por assert) e o delta tem que ser inspecionado à mão — janela maior gera mais falso
positivo, e o número só é aceitável se for revisável pela mesa.

## Cobrir os 30 clientes

Hoje só os 10 mais sinalizados recebem parecer, por rate limit. Com o cache por hash acima, mais
processamento em fila respeitando o TPM, o lote completo roda — inclusive para reavaliar se
clientes **não** sinalizados pelas regras teriam sido pegos pelo agente, que é o falso negativo
que nenhuma métrica atual mede.
