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

| Métrica | Execução atual |
|---|---|
| Respostas válidas | **7/10** (3 esgotaram `max_turnos`) |
| Concordância entre as válidas | **14%** (1/7) |
| Divergências qualitativas | 6 |

### A divergência é sistemática, não aleatória — e essa é a informação

Os **6 divergentes vão todos na mesma direção**: a regra diz `alto`, o agente diz `médio`. Zero
casos na direção oposta. Isso não é ruído, é **diferença de limiar**: meu critério dispara "alto"
com um único sinal (fracionamento *ou* 2+ atípicas), enquanto o agente parece reservar "alto"
para quando múltiplos fatores se reforçam. Nenhum dos dois é "a verdade" — são dois desenhos de
threshold, e o fato de a discordância ser unidirecional é o dado interessante. Se fosse
aleatória, apontaria para modelo instável; sendo sistemática, aponta para calibração.

O caso mais didático é `CLI-029`: o parecer **reconhece explicitamente** o fracionamento
("4 operações em 2026-05-26, totalizando R$ 71.297,68... indica possível tentativa de evitar
reporte") e mesmo assim classifica como médio. Ele não errou o fato; discordou da gravidade.

### Mas a verificação de aderência mostrou que "discordar bem" não é a história toda

Aqui as duas análises se cruzam, e o resultado é menos favorável ao agente do que a leitura
acima sugeriria isolada: dos 7 pareceres válidos, **3 estavam fundamentados na operação errada**
(ver seção de verificação de aderência). Ou seja, parte da "divergência de limiar" foi produzida
por um raciocínio ancorado no número errado — `CLI-013` classificou como médio depois de apontar
como "atípica" uma operação de R$ 312,54, a menor da base do cliente.

**A conclusão honesta**: a divergência sistemática é real e interessante, mas eu não posso
afirmar que ela representa um julgamento melhor que o da regra, porque em 3 dos 7 casos o
julgamento partiu de premissa factualmente errada. É exatamente por isso que a verificação de
aderência precisa rodar **antes** de qualquer análise de divergência — sem ela, eu teria escrito
uma seção elogiando o discernimento do agente.

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
motivo. Esse comportamento secundário (o modelo chutar uma data quando não tem uma fornecida)
fica registrado como limitação nova, não resolvida agora — ver seção 2.

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
- **`max_turnos=4` pode não bastar.** Mais de um cliente já esgotou o limite sem responder — o
  tratamento de malformado funcionou, mas o caso não chegou a parecer nenhum.
- **O agente inventa data quando não recebe uma.** Para clientes sem `flag_fracionamento` (que
  não têm `datas_fracionamento` no prompt), o modelo às vezes chama `operacoes_do_dia` mesmo
  assim, com uma data chutada sem grounding nos dados — comportamento que ajudou a esgotar
  `max_turnos` em 3 dos 10 clientes numa execução. A correção simétrica à do fracionamento seria
  a mesma ideia: ou o prompt deixa mais explícito que a ferramenta só deve ser usada quando há
  uma data fornecida, ou o agente aprende (via few-shot) que "investigar sem pista de data" é
  uma decisão válida por si só, não motivo para adivinhar.

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
