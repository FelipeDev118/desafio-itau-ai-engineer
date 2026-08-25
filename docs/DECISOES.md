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
estatístico. Resultado: **50% de concordância (5/10)**, com divergências nas duas direções.

### A análise das divergências não deu o resultado que eu esperava

O enunciado sugere que um agente que discorda com boa justificativa pode estar certo. Conferindo
caso a caso contra os dados, encontrei o contrário em dois:

- **`CLI-005`** (regra: alto · agente: médio). O parecer fundamenta o risco em *"a operação de
  **2024**-05-07 (R$ 409,16)"*. Dois erros verificáveis: o ano é **2026**, e — muito pior —
  **R$ 409,16 não é a operação atípica**, é um valor *abaixo* da mediana do cliente
  (R$ 2.144,18). As operações realmente sinalizadas são `OP-00049` (R$ 11.988,17) e `OP-00043`
  (R$ 30.743,97). O agente raciocinou sobre a operação errada e mesmo assim produziu um parecer
  que **soa** plausível. A regra estava certa, o agente errado.
- **`CLI-017`** (regra: alto · agente: médio). O parecer nomeia a tipologia como **smurfing** e
  então classifica o risco como *médio* — internamente inconsistente.
- Na direção oposta, **`CLI-030`** (regra: médio · agente: alto) é escalada **legítima**: o
  agente notou que R$ 85.546,51 de R$ 117.780,89 estão concentrados em duas TEDs, concentração
  que a Regra 2, comparando operação a operação contra a mediana, não captura.

**A conclusão que levo é sobre a forma, não o placar**: a justificativa em linguagem natural é
persuasiva *independentemente de estar correta*. Os pareceres errados são bem escritos, citam
números e soam técnicos. Num fluxo real, um analista lendo só o parecer não teria como perceber
que a operação citada é a errada. É o argumento mais concreto desta entrega a favor de manter a
camada determinística: ela é o que torna o modelo **auditável**.

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

## O parecer não é reproduzível — e em PLD isso é problema de compliance

A comparação entre transportes (Nível 3) expôs isto sem que eu procurasse: **o mesmo agente, com
as mesmas ferramentas e os mesmos dados, atribuiu `nivel_risco` diferente para 4 de 10 clientes
entre duas execuções**. `CLI-014` saiu `alto` numa e `médio` na outra, com `temperature=0.2`.

Uma classificação de risco precisa ser auditável: um analista tem que poder explicar por que o
cliente foi classificado como alto risco *naquela data*, e reexecutar deveria chegar ao mesmo
lugar. Não chega. Ver a correção proposta na seção 3.

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
- **Custo medido por cliente, não por chamada.** Agrego os turnos, então não sei qual turno
  (decisão vs. redação) consome mais — que é exatamente o que eu precisaria para otimizar.
- **Só os 10 mais sinalizados passam pelo agente**, por causa do rate limit; os outros 20
  clientes não recebem parecer.

---

# 3. O que faria com mais tempo

## Verificação de aderência do parecer aos dados (prioridade 1)

O problema do `CLI-005` — parecer bem escrito fundamentado na operação errada — é o mais grave
que encontrei, porque passa despercebido por revisão humana.

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

## Reprodutibilidade do parecer

**Arquitetura**: `temperature=0` e `seed` fixo quando o provedor suportar — mas isso é paliativo.
A correção de verdade é **tratar o parecer como artefato imutável**: persistir cada parecer com o
hash da entrada (dados + flags + versão do prompt + modelo) que o gerou. A decisão de risco vira
registro histórico datado, não função recalculável que pode responder diferente amanhã.

**Ferramenta**: SQLite com `hash_entrada` como chave — resolve de uma vez a reprodutibilidade e o
cache que o enunciado sugere, já que parecer existente para o mesmo hash é reaproveitado em vez
de repedido.

**Como validaria**: rodar o lote duas vezes seguidas e exigir 10/10 idênticos — hoje dá 6/10. E
alterar um único valor da base para confirmar que o hash muda e o parecer é regerado (senão o
cache estaria mascarando dado novo).

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
