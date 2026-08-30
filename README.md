# Desafio Técnico — Estágio em Engenharia de IA

Triagem de operações financeiras para Prevenção à Lavagem de Dinheiro (PLD/AML), combinando
**regras determinísticas em pandas** (o que é cálculo) com um **LLM** (o que é interpretação e
redação de parecer).

O princípio que guia todo o projeto: soma, mediana, contagem e comparação com limite são feitas
em pandas e **entregues prontas ao modelo como fato**; o LLM nunca calcula nem decide se um
número ultrapassou um limite — ele interpreta o padrão e redige o parecer.

## Como rodar

### Com Docker (recomendado)

```bash
cp .env.example .env                  # preencha GROQ_API_KEY
docker compose run --rm verificar     # smoke test: não precisa de chave nem chama LLM
docker compose up notebook            # Nível 1 em http://localhost:8888
docker compose run --rm nivel2        # regras em escala + lote + confronto
docker compose run --rm nivel3        # agente via MCP + comparação de transportes
```

O `verificar` reexecuta a camada determinística e compara com os números desta entrega —
é a prova de que o pipeline de regras reproduz em qualquer máquina. Detalhes e o que ele
deliberadamente **não** garante em [`docs/REPRODUTIBILIDADE.md`](docs/REPRODUTIBILIDADE.md).

### Sem Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # preencha GROQ_API_KEY com sua chave
python verificar_ambiente.py
```

Nível 1 (notebook, já commitado com as saídas executadas):

```bash
jupyter notebook nivel_1/nivel_1.ipynb
```

Nível 2 (a partir da pasta `nivel_2/`):

```bash
cd nivel_2
python dados.py        # regras em escala + top 10 clientes sinalizados
python lote.py         # roda o agente sobre os 10 clientes -> outputs/
python confronto.py    # confronta regra vs. agente -> outputs/
```

Nível 3 — MCP (a partir da raiz do projeto; o cliente sobe o servidor sozinho):

```bash
python nivel_3/agente_mcp.py --lote        # agente consumindo as tools por MCP
python nivel_3/comparar_transportes.py     # valida MCP vs. import direto
```

## Estrutura

| Caminho | O que é |
|---|---|
| `nivel_1/nivel_1.ipynb` | Tratamento de dados, regras determinísticas, validação e parecer via LLM (com as saídas executadas). |
| `nivel_2/dados.py` | Carga, limpeza e regras reaproveitadas do Nível 1 sobre a base maior. |
| `nivel_2/tools.py` | As três ferramentas que o agente pode consultar. |
| `nivel_2/agente.py` | Agente com function calling nativo — o modelo decide quais tools chamar. |
| `nivel_2/lote.py` | Execução em lote sobre os 10 clientes mais sinalizados + métricas. |
| `nivel_2/confronto.py` | Confronto entre `nivel_risco` do agente e as flags determinísticas. |
| `nivel_3/mcp_server.py` | Servidor MCP (stdio) que republica as ferramentas do Nível 2. |
| `nivel_3/agente_mcp.py` | Agente que consome as ferramentas por MCP, não por import direto. |
| `nivel_3/comparar_transportes.py` | Valida que a troca de transporte preserva o comportamento. |
| `outputs/` | Resultados salvos de todas as execuções. |
| `docs/DECISOES.md` | Trade-offs, limitações e o que faria com mais tempo. |
| `docs/USO_DE_IA.md` | Como usei IA e onde ela me levou ao caminho errado. |

## O que foi concluído

- **Nível 1 — completo.** Limpeza (3 problemas de qualidade encontrados e tratados), duas regras
  determinísticas, validação explícita da Regra 1 (caso positivo vs. caso parecido que não se
  enquadra), parecer estruturado validado com Pydantic e tratamento de resposta malformada,
  duas versões de prompt comparadas com métricas de tokens e latência.
- **Nível 2 — completo.** Regras reaproveitadas em escala sem reescrita, três ferramentas,
  agente com function calling nativo (usa 3 padrões distintos de ferramentas entre os 10
  clientes — só 6 dos 10 receberam as três), lote sobre os 10 clientes com registro de
  custo/latência, e confronto regra vs. modelo com análise das divergências.
- **Nível 3 — completo (Trilha B).** As ferramentas do Nível 2 são expostas por um servidor MCP
  local via stdio e consumidas pelo protocolo, com descoberta em runtime — o agente não tem mais
  a lista de ferramentas hardcoded. Validado comparando as duas vias: payload das ferramentas
  idêntico em 6/6 casos. Arquitetura e instruções de conexão em
  [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md).

## Alguns achados da execução

- O **prompt v1 do Nível 1 é instável**, não simplesmente errado: executado duas vezes com o
  mesmo texto e `temperature=0.2`, numa delas alucinou um limite de reporte de "≈ R$ 10.000" que
  não existe no enunciado (o real é R$ 20.000,00) e na outra acertou. Por ser subespecificado,
  ele deixa o modelo preencher a lacuna com conhecimento genérico. O v2 não tem essa lacuna
  porque recebe o número já calculado. A execução original está no commit `9a59dd8`.
- O modelo tenta, ocasionalmente, chamar uma **ferramenta fictícia chamada `JSON`** para devolver
  a resposta final, o que a API rejeita; o parecer válido vem dentro do corpo do erro e é
  recuperado de lá (`nivel_2/agente.py`).
- O agente **decide** quais ferramentas usar (3 padrões distintos entre os 10 clientes), mas a
  auditoria das saídas revelou um **defeito de desenho do meu prompt**: clientes com flag de
  fracionamento não consultam o recorte diário, porque o prompt informa *que* a flag está ativa
  sem informar *em qual data*. Analisado em
  [`docs/DECISOES.md`](docs/DECISOES.md#nível-2--agente-e-ferramentas).
- A **concordância entre regra e agente variou entre execuções** (50% numa rodada, 30% noutra,
  mesmo código e dados) — essa instabilidade em si é o achado mais importante: numa rodada, o
  parecer de `CLI-005` citou uma operação (R$ 409,16) que não é a sinalizada pela Regra 2; na
  outra, citou as corretas. Na maioria das divergências qualitativas, o agente reconhece o mesmo
  padrão que disparou a regra e classifica abaixo assim mesmo — um limiar mais conservador para
  "alto", não um erro de fato. Análise em
  [`docs/DECISOES.md`](docs/DECISOES.md#nível-2--confronto-regra-vs-modelo).
- **Cache por hash de entrada** (`nivel_2/cache_parecer.py`) resolve essa instabilidade *entre
  reexecuções do pipeline*: parecer já gerado é reaproveitado em vez de recalculado. Provado, não
  só afirmado — rodar `lote.py` a 3ª vez levou 1,3s com **0 chamadas de API** (a 1ª levou 3min47),
  e `confronto.py` passou a dar o mesmo número em execuções seguidas.
