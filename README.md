# Desafio Técnico — Estágio em Engenharia de IA

Triagem de operações financeiras para Prevenção à Lavagem de Dinheiro (PLD/AML), combinando
**regras determinísticas em pandas** (o que é cálculo) com um **LLM** (o que é interpretação e
redação de parecer).

O princípio que guia todo o projeto: soma, mediana, contagem e comparação com limite são feitas
em pandas e **entregues prontas ao modelo como fato**; o LLM nunca calcula nem decide se um
número ultrapassou um limite — ele interpreta o padrão e redige o parecer.

## Como rodar

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # preencha GROQ_API_KEY com sua chave
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

## Estrutura

| Caminho | O que é |
|---|---|
| `nivel_1/nivel_1.ipynb` | Tratamento de dados, regras determinísticas, validação e parecer via LLM (com as saídas executadas). |
| `nivel_2/dados.py` | Carga, limpeza e regras reaproveitadas do Nível 1 sobre a base maior. |
| `nivel_2/tools.py` | As três ferramentas que o agente pode consultar. |
| `nivel_2/agente.py` | Agente com function calling nativo — o modelo decide quais tools chamar. |
| `nivel_2/lote.py` | Execução em lote sobre os 10 clientes mais sinalizados + métricas. |
| `nivel_2/confronto.py` | Confronto entre `nivel_risco` do agente e as flags determinísticas. |
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
- **Nível 3 — não feito.** Trilha que escolheria (B, servidor MCP) e plano de ataque estão em
  [`docs/DECISOES.md`](docs/DECISOES.md#nível-3).

## Alguns achados da execução

- O **prompt v1 do Nível 1 alucinou** um limite de "R$ 10.000" que não existe no enunciado — a
  execução foi mantida no notebook como evidência de por que o cálculo deve ficar fora do LLM.
- O modelo tenta, ocasionalmente, chamar uma **ferramenta fictícia chamada `JSON`** para devolver
  a resposta final, o que a API rejeita; o parecer válido vem dentro do corpo do erro e é
  recuperado de lá (`nivel_2/agente.py`).
- O agente **decide** quais ferramentas usar (3 padrões distintos entre os 10 clientes), mas a
  auditoria das saídas revelou um **defeito de desenho do meu prompt**: clientes com flag de
  fracionamento não consultam o recorte diário, porque o prompt informa *que* a flag está ativa
  sem informar *em qual data*. Analisado em
  [`docs/DECISOES.md`](docs/DECISOES.md#nível-2--agente-e-ferramentas).
- A **concordância entre regra e agente foi de 50%**, mas o achado relevante está nas
  divergências: em 2 dos 5 casos **a regra estava certa e o agente errado** — em `CLI-005` o
  parecer fundamenta o risco numa operação de R$ 409,16 que está *abaixo* da mediana do cliente
  (a operação realmente atípica era outra, de R$ 30.743,97) e ainda alucina o ano da data. O
  texto soa técnico e plausível mesmo estando errado, o que é o argumento mais concreto desta
  entrega a favor de manter a camada determinística auditável. Análise em
  [`docs/DECISOES.md`](docs/DECISOES.md#nível-2--confronto-regra-vs-modelo).
