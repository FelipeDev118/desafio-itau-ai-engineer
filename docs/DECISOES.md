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
estatístico.

### A métrica precisou ser desmembrada para não mentir

A primeira versão reportava uma `taxa_concordancia` única. Ela **misturava duas falhas de
natureza diferente**: "o agente discordou da regra" e "o agente não produziu parecer nenhum"
(esgotou `max_turnos`). Um número só, somando as duas, mede menos do que aparenta — uma queda na
taxa pode significar tanto divergência de critério quanto instabilidade técnica, e são problemas
com soluções opostas. `confronto.py` hoje reporta os dois separados:

| Métrica | Execução atual (pós-correção do chute de data, ver seção 2) |
|---|---|
| Respostas válidas | **10/10** (0 esgotaram `max_turnos` — antes da correção: 7/10) |
| Concordância entre as válidas | **40%** (4/10 — antes: 14%, 1/7) |
| Divergências qualitativas | 6 |

A subida de 14% para 40% é sobretudo efeito do denominador (7→10 respostas válidas), não prova
de que o agente calibrou melhor: são 6 divergentes nas duas execuções, só que agora sobre uma base
maior. Ver nota de não-determinismo abaixo — não comparar essas duas execuções como "antes/depois"
controlado, o prompt mudou entre elas.

### A divergência é sistemática, não aleatória — e essa é a informação

Os **6 divergentes vão todos na mesma direção**: a regra diz `alto`, o agente diz `médio`. Zero
casos na direção oposta. Isso não é ruído, é **diferença de limiar**: meu critério dispara "alto"
com um único sinal (fracionamento *ou* 2+ atípicas), enquanto o agente parece reservar "alto"
para quando múltiplos fatores se reforçam. Nenhum dos dois é "a verdade" — são dois desenhos de
threshold, e o fato de a discordância ser unidirecional é o dado interessante. Se fosse
aleatória, apontaria para modelo instável; sendo sistemática, aponta para calibração.

Um caso didático da execução anterior (`CLI-029`) ilustra bem o padrão mesmo não sendo mais
divergente nesta reexecução: o parecer **reconhecia explicitamente** o fracionamento
("4 operações em 2026-05-26, totalizando R$ 71.297,68... indica possível tentativa de evitar
reporte") e mesmo assim classificava como médio — não errava o fato, discordava da gravidade.
Na execução atual esse mesmo cliente concorda com a regra (`alto`/`alto`) — outra confirmação de
que a fronteira entre "médio" e "alto" não é estável no agente, é a mesma flag e o mesmo cliente
mudando de lado entre rodadas.

### Mas a verificação de aderência mostrou que "discordar bem" não é a história toda

Aqui as duas análises se cruzam. Numa primeira leitura, logo após corrigir o esgotamento de
turnos, o resultado parecia pior do que antes: só 3 dos 10 pareceres válidos fundamentados,
contra 4/7 (~57%) na execução anterior. Essa leitura **estava errada** — não porque o agente
piorou, mas porque o próprio verificador tinha 3 bugs que inflavam falsos "não fundamentado"
(ver seção abaixo, "O verificador de aderência tinha bugs próprios"). Corrigidos os bugs, o
número real é **8/10 fundamentados** — melhor do que a execução anterior, não pior.

Dos 2 casos que continuam não fundamentados, só 1 é um erro real do agente: `CLI-028` aponta como
"único evento de valor atípico" uma operação de R$ 6.913,84, mas as operações que de fato carregam
`flag_valor_atipico=True` para esse cliente são de R$ 27.715,48 e R$ 24.875,39 — mais que o dobro
do valor citado. O outro (`CLI-001`) não é um erro de fundamentação, é a ausência dela: o parecer
não cita nenhum valor em R$, só qualificadores vagos ("valores médios significativamente
superiores ao mediano") — não há o que verificar, para o bem ou para o mal.

**A conclusão honesta, revisada**: a divergência sistemática (regra `alto` → agente `médio`)
continua real, e agora há evidência mais forte de que não é fruto de raciocínio mal fundamentado
— 8 dos 10 pareceres partem de premissas corretas. Isso não prova que o critério do agente é
"melhor" que o da regra (são dois desenhos de threshold, ver acima), mas derruba a hipótese mais
fraca de que a divergência era apenas erro de leitura dos dados. E o episódio deixa uma lição
maior que o número em si: a primeira medição pós-correção **também precisava ser auditada antes
de virar afirmação** — quase escrevi "consertar o max_turnos piorou a aderência" com base num
verificador que, ele mesmo, não tinha sido verificado.

### Nota de não-determinismo

Execuções anteriores deste mesmo confronto, com o **mesmo código e os mesmos dados**, deram
**50%** e depois **30%** de concordância (ambas preservadas no histórico do git). Numa delas o
parecer de `CLI-005` citava R$ 409,16 como a operação atípica — valor abaixo da mediana, portanto
errado; noutra, citava as corretas. Isso significa que **este próprio documento estaria diferente**
a cada reexecução, e foi o que motivou o cache por hash descrito adiante. Os números desta seção
valem para as saídas commitadas em `outputs/`, que agora são estáveis justamente porque o parecer
deixou de ser recalculado a cada rodada.

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

## Verificação de aderência: o parecer cita números que existem de verdade?

O problema que motivou isto: numa execução anterior, o parecer de `CLI-005` citou "a operação de
2024-05-07 (R$ 409,16)" como a operação atípica. Bem escrito, com números, soando técnico — e
errado em dois níveis (o ano é 2026, e R$ 409,16 está *abaixo* da mediana do cliente, não é a
operação sinalizada). Um analista lendo só a justificativa não teria como perceber.

**O que implementei** (`nivel_2/verificacao_aderencia.py`): um *grounding check* determinístico
que roda depois do agente e antes de gravar o parecer. Extrai da `justificativa` todo valor em
R$ e confere contra os dados reais do cliente — operações individuais, somas por data e
agregados (soma/média/mediana). **Deliberadamente sem LLM**: usar um modelo para auditar outro
reintroduziria exatamente o problema que estamos tentando pegar.

**A parte que só apareceu ao validar contra dados reais** — e que mudou o desenho: checar
*existência* não basta. No caso `CLI-005`, R$ 409,16 **existe** na base (é a operação
`OP-00041`), então uma verificação ingênua o aprovaria. O erro não foi inventar o número, foi
**usá-lo no contexto errado**. Por isso a verificação também confere contexto: quando o texto
liga um valor à palavra "atípic*", esse valor precisa ser de uma operação com
`flag_valor_atipico=True`. Foi o que finalmente pegou o erro pelo motivo certo.

**Dois falsos positivos encontrados e corrigidos na validação**, ambos instrutivos:
- *Somas por data*: `CLI-029` citava "4 operações em 26/05, totalizando R$ 71.297,68" — valor
  legítimo que eu não sabia procurar, porque só checava agregados do cliente inteiro. Somas
  diárias entraram como referência válida.
- *Limiares textuais*: `CLI-013` dizia "valores superiores a R$ 3.000" — isso é uma qualificação
  vaga, não uma transação citada. Filtrados por palavras-gatilho ("superior a", "acima de",
  "cerca de"…) antes do valor.
- Também precisei aceitar que o modelo escreve valor **sem `R$`**, com sufixo `BRL`, e em
  formato numérico inconsistente entre chamadas (`21.261,01 BRL` em padrão BR, `5016.62 BRL`
  em padrão americano) — o parser decide o formato pela presença de vírgula.

**O resultado, que é o achado mais forte desta rodada**: dos 7 pareceres válidos do lote,
**3 estavam fundamentados na operação errada** — `CLI-014`, `CLI-013` e `CLI-001` apontaram como
"atípica" uma operação sem a flag. O caso `CLI-013` é o mais gritante: chamou de atípica uma
operação de **R$ 312,54**, a menor da base do cliente, quando as realmente sinalizadas são de
R$ 28.487,76 e R$ 26.754,23. Nenhum desses erros é perceptível lendo o texto — todos soam
plausíveis. É a evidência mais concreta de por que a camada determinística não pode ser
substituída pelo LLM: ela é o que permite **auditar** o modelo.

**Limite honesto desta verificação**: ela confere existência e um contexto específico
("atípico"), não o raciocínio inteiro. Um parecer pode citar todos os números corretos e ainda
assim concluir mal — isso ela não pega. E o filtro de contexto é baseado em palavra-chave, então
uma frase que descreva atipicidade sem usar a palavra escapa. É uma rede de segurança barata e
sem LLM, não um juiz completo.

## O agente não usava `operacoes_do_dia` nos casos de fracionamento — corrigido

Achado da auditoria original: 7 clientes *sem* `flag_fracionamento` chamavam
`operacoes_do_dia`, e `CLI-029`, que *tem* a flag — o caso onde essa ferramenta serve de verdade
— não chamava. A causa era um defeito de desenho meu: o prompt informava *que* a flag estava
ativa, mas não *em qual data*, e sem data o agente não tinha o que passar para a ferramenta.

**Correção** (`nivel_2/dados.py`, função `datas_fracionamento`): recuperar do próprio cálculo de
`flag_fracionamento()` as datas que dispararam a regra para cada cliente — o dado já existia, só
era descartado ao colapsar tudo num booleano. Uma função nova, `montar_flags()`, centraliza a
montagem do dicionário de flags que vai para o prompt, porque essa montagem estava duplicada em
três lugares (`lote.py`, o teste standalone de `agente.py`, e `nivel_3/agente_mcp.py`) — e foi
justamente por causa dessa duplicação, uma vez, que um lugar recebeu a data e os outros não.
Também dei bump em `VERSAO_PROMPT` no cache, porque o `SYSTEM_PROMPT` mudou de verdade e
pareceres antigos (gerados com a instrução incompleta) não deveriam ser reaproveitados.

**Prova**: reexecutei o lote. Dos 2 clientes com fracionamento que entraram no top 10 desta vez
(`CLI-017`, `CLI-029`), os **2** chamaram `operacoes_do_dia` exatamente na data sinalizada pela
Regra 1 — antes, 0 chamavam. O parecer de `CLI-029` passou a citar números concretos e
verificáveis (4 operações em 26/05, entre R$ 14.326,29 e R$ 19.418,96) em vez de descrever o
padrão em termos vagos.

**Efeito colateral que a correção do confronto expôs**: com a métrica antiga (`taxa_concordancia`
misturando "parecer discordante" e "parecer que nem existiu"), essa execução teria mostrado
"10% de concordância" de forma enganosa — 3 dos 10 clientes ficaram sem parecer válido
(`numero maximo de turnos excedido`), não porque discordaram da regra, mas porque o modelo
esgotou os turnos disponíveis chamando `operacoes_do_dia` repetidamente com datas **inventadas**
para clientes sem `flag_fracionamento` (que não recebem `datas_fracionamento` no prompt).
Corrigi `nivel_2/confronto.py` para separar "respostas válidas" de "taxa de concordância entre
as válidas" — a métrica de 10% escondia que 30% das chamadas simplesmente falharam por outro
motivo. Esse comportamento secundário (o modelo chutar uma data quando não tem uma fornecida) ficou
registrado como limitação, e foi corrigido depois — ver "O agente chutava data em
`operacoes_do_dia`... — corrigido", abaixo.

## O agente chutava data em `operacoes_do_dia` e esgotava `max_turnos` — corrigido

Achado da auditoria original (`outputs/pareceres_lote.json` da entrega): 3 dos 10 clientes
(`CLI-023`, `CLI-005`, `CLI-030`) esgotavam `max_turnos=4` sem produzir parecer. Os três têm
`flag_valor_atipico=True` e `flag_fracionamento=False` — ou seja, **nenhuma data vem nas flags**.
Inspecionando `tools_chamadas` de cada um: o agente chamava `operacoes_do_dia` três vezes
seguidas, cada vez com uma data diferente e nenhuma delas fornecida ou observada em qualquer
retorno de ferramenta anterior — puro chute, "pescando" um dia que desse informação. Isso
consumia os 4 turnos sem sobrar um para a resposta final.

**Correção** (`nivel_2/agente.py`, `SYSTEM_PROMPT`): a regra de uso de `operacoes_do_dia` deixou
de assumir que "não inventar data" só importa quando `datas_fracionamento` está presente.
Agora o prompt é explícito nos dois lados: use a data das flags quando houver, ou uma data já
*observada* em outra ferramenta (ex.: `data_min`/`data_max` de `historico_cliente`); se não há
nenhuma data concreta disponível, **não chame a ferramenta** — produzir o parecer sem esse dado
é uma decisão válida, não motivo para adivinhar. Bump em `VERSAO_PROMPT` (`v2` → `v3`) para não
reaproveitar pareceres cacheados com a instrução antiga.

**Validação contra a API real** (não simulada), em duas etapas:

1. Rodei os 3 clientes que historicamente esgotavam turno, duas vezes, sem cache. Nas 6 execuções
   (2 × 3 clientes): 0 chamadas a `operacoes_do_dia`, 0 esgotamentos de `max_turnos`, 6/6 pareceres
   produzidos (`historico_cliente` sozinho, ou seguido de `perfil_canal` — nunca uma data chutada).
2. Reexecutei o lote completo dos 10 clientes (`nivel_2/lote.py`, sem cache aproveitável — o bump
   de `VERSAO_PROMPT` invalidou as entradas antigas) e reauditei com `confronto.py` e
   `verificacao_aderencia.py`. Resultado: **10/10 respostas válidas, 0 esgotamentos** (era 7/10,
   3 esgotando). A primeira leitura da aderência (3/10 fundamentados) **estava errada por bug do
   verificador**, não por regressão do agente — corrigido o verificador, o número real é 8/10.
   Ver "O verificador de aderência tinha bugs próprios", abaixo, e "Mas a verificação de aderência
   mostrou que 'discordar bem' não é a história toda", acima, para os dois lados dessa história.
   Números atualizados no README e na tabela de confronto desta seção.

## O verificador de aderência tinha bugs próprios — corrigido

Ao investigar por que a aderência parecia ter piorado depois do fix de `max_turnos` (3/10 contra
4/7 antes), auditei os 7 pareceres marcados como "não fundamentado" um a um contra os dados reais
— o mesmo cuidado que o `verificacao_aderencia.py` deveria estar aplicando ao agente. Achado: 3
bugs no próprio verificador (`nivel_2/verificacao_aderencia.py`), nenhum no agente:

1. **Mediana confundida com operação individual.** Quando o cliente tem número ímpar de
   operações, a mediana é, por definição matemática, igual ao valor de uma operação real do meio
   da distribuição — não coincidência. `_referencias_validas()` registrava as operações antes dos
   agregados, então a busca por `fonte` retornava "operação X" para esse valor, e o filtro de
   contexto (`citado_como_atipico` + `fonte` começa com "operação") acusava falso "atípico
   incorreto" sempre que a frase mencionava a mediana perto da palavra "atípico" — mesmo sendo
   uma observação genérica ("valor mediano de R$X, indicando presença de transações atípicas"),
   não uma citação daquela operação específica. Afetou `CLI-014` e `CLI-005` (ambos com 11
   operações). **Correção**: agregados (volume/média/mediana), depois somas por dia e por canal,
   depois operações individuais — nesta ordem — para que um empate de valor resolva para o
   agregado, a leitura mais provável quando o texto diz "mediano"/"médio"/"total".
2. **Regex truncava formato americano e abreviação "k".** O modelo escreveu, na mesma execução,
   `R$71,297.68` (milhar por vírgula, decimal por ponto — formato americano) e `R$14.3k`
   (abreviação de mil). O regex só entendia o formato BR (`14.326,29`) e cortava os outros no
   meio: `71,297.68` virava `71.29`, `14.3k` virava `14.0`. Um parecer **correto** (o valor citado
   era real) parecia "não fundamentado" só por bug de parsing. Afetou `CLI-029` e `CLI-017`.
   **Correção**: alternativas de regex para milhar por ponto (BR) e por vírgula (US), decisão de
   formato pelo separador que aparece **por último** na string, e captura do sufixo `k` com
   multiplicação por 1000.
3. **Soma por canal não existia como referência.** `CLI-030` citou "R$85.546,51 concentrados em
   duas operações TED" — valor real (soma exata de duas operações reais do canal TED), mas
   `_referencias_validas()` só conhecia somas por data (fracionamento) e agregados do cliente
   inteiro, nunca por canal — embora `perfil_canal()` seja uma das 3 ferramentas do agente e
   devolva exatamente esse número. **Correção**: soma por canal com 2+ operações, no mesmo
   espírito da soma por dia que já existia.

Um quarto ajuste, descoberto no mesmo processo: `CLI-029`/`CLI-017` também descreviam faixas
aproximadas ("entre R$14.3k e R$19.4k", arredondando os extremos reais 14.326,29 e 19.418,96) —
nem bug, nem citação exata, é a mesma categoria dos qualificadores textuais que o verificador já
tratava ("superior a R$X"). Estendi `QUALIFICADORES` para reconhecer "entre" e propaguei o
`e_limiar` do primeiro limite da faixa para o segundo.

**Validação**: reaudita dos mesmos 10 pareceres (sem chamar a API de novo — só a lógica de
grounding mudou) após cada correção: 3/10 → 5/10 (bug 1) → 6/10 (bug 3) → 8/10 (bug 2 + faixa).
Testes de regressão para os 4 casos em `tests/test_verificacao_aderencia.py`. Dos 2 que continuam
não fundamentados, 1 é erro real do agente (`CLI-028`) e 1 não tem valor citável nenhum
(`CLI-001`, correto por definição, não um bug).

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

## Testes automatizados das regras — corrigido

`tests/` (pytest): 23 testes cobrindo os limites onde regra de negócio quebra — operação de
exatamente R$ 20.000,00, soma de exatamente R$ 50.000,00, cliente com exatamente 3 e exatamente 4
operações, cliente com todas as datas nulas, valor exatamente no limite de atipicidade (mediana ×
5). Mais 4 testes de integração do agente com o cliente Groq mockado (`tests/test_agente.py`):
tool-call seguido de parecer válido, cache de parecer (segunda chamada não bate na API), esgotar
`max_turnos` sem resposta final, e parecer fora do schema Pydantic. Roda sem rede nem rate limit —
`python -m pytest` leva ~1,5s.

Cada teste de limite falharia se alguém trocasse `>` por `>=` (ou vice-versa) nas regras — o que
não existia antes disso. Rodar: `source .venv/bin/activate && python -m pytest -v`.

## Outras

- ~~**Sem testes automatizados.**~~ Resolvido — ver acima.
- **Acoplado a um provedor.** O tratamento do bug da "tool `JSON`" é específico do
  `gpt-oss` via Groq; trocar de provedor exige revisitar essa parte.
- **Só os 10 mais sinalizados passam pelo agente**, por causa do rate limit; os outros 20
  clientes não recebem parecer, e não há como saber se algum deveria ter sido pego.
- ~~**`max_turnos=4` pode não bastar** / **agente inventa data quando não recebe uma.**~~
  Resolvido — ver "O agente chutava data em `operacoes_do_dia`..." acima: validado nos 3 clientes
  que historicamente esgotavam turno e confirmado no lote completo re-executado (10/10 respostas
  válidas).

---

# 3. O que faria com mais tempo

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
