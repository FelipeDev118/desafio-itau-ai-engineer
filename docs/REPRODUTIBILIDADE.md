# Reprodutibilidade — "na minha máquina funciona"

## O problema, e por que a resposta óbvia não serve

A preocupação usual é: *o avaliador clona o repositório, roda, dá erro de versão de biblioteca, e
o trabalho é julgado por um problema de ambiente.* Containerizar é a resposta padrão.

Só que, neste caso específico, ela resolve menos do que parece — por duas razões que vale
enunciar:

1. **O enunciado diz que o código não será executado** (*"nós não vamos executar seu código"*) e
   que containers não são exigidos. Então o container não está aqui para destravar a correção.
2. **Mais importante: metade deste sistema não é reproduzível nem na mesma máquina.** Os
   pareceres vêm de um LLM. Rodar duas vezes seguidas, no mesmo hardware, com o mesmo código e
   `temperature=0.2`, produziu `nivel_risco` diferente para **4 de 10 clientes** — está medido em
   [`ARQUITETURA.md`](ARQUITETURA.md). Nenhum Dockerfile conserta isso.

Ou seja, "funciona igual em qualquer máquina" não é uma afirmação que eu possa fazer sobre o
sistema inteiro, e prometê-la seria desonesto. O que dá para fazer é **delimitar exatamente o que
é reproduzível e provar essa parte**.

## O que é reproduzível, e a prova

A camada determinística — limpeza, Regra 1, Regra 2, ranking — é pandas puro sobre um arquivo
versionado. Ela **tem** que dar o mesmo resultado em qualquer lugar, e é isso que
[`verificar_ambiente.py`](../verificar_ambiente.py) checa: reexecuta o pipeline e compara com os
valores obtidos na execução que gerou os arquivos de `outputs/`.

```bash
docker compose run --rm verificar     # no container
python verificar_ambiente.py          # local, sem Docker
```

Não precisa de chave de API — não chama LLM nenhum. Resultado esperado:

```
verificacao                      esperado       obtido
operacoes_brutas                      322          322   OK
duplicatas_removidas                    5            5   OK
datas_nulas                             7            7   OK
operacoes_usd                           7            7   OK
operacoes_apos_limpeza                317          317   OK
clientes_fracionamento                  4            4   OK
operacoes_atipicas                     21           21   OK

top 10 clientes sinalizados: OK

OK: a camada deterministica reproduz exatamente os numeros da entrega.
```

Verificado em duas condições: no ambiente de desenvolvimento (Python 3.12.3, pandas 3.0.5) e
dentro da imagem Docker construída do zero — resultados idênticos.

O script **falha com código de saída 1** se algum número divergir, e diz explicitamente que o
resto da entrega deve ser lido com desconfiança nesse caso. É um teste de fumaça, não um enfeite.

Note que ele também trata a parte não-reproduzível com honestidade: reporta a distribuição de
`nivel_risco` dos pareceres commitados como **informativo**, deixando claro que não é verificada,
porque comparar parecer contra parecer seria um teste que falha pelo motivo errado.

> Nota de método: os valores esperados neste script foram *derivados* da execução real, não
> estimados. Na primeira versão eu preenchi dois deles de cabeça (`2` clientes com fracionamento e
> `15` operações atípicas); o próprio script reprovou, e os valores corretos eram `4` e `21`.
> Ficou como exemplo do que o resto desta entrega vem repetindo: número que não foi conferido
> contra os dados não vale como afirmação.

## Como rodar

Pré-requisito: `.env` na raiz com `GROQ_API_KEY` (use `.env.example` como molde). Ele **não** está
no repositório nem entra na imagem — a chave é injetada em runtime.

```bash
docker compose run --rm verificar     # smoke test, sem chave e sem LLM
docker compose up notebook            # Nível 1 em http://localhost:8888
docker compose run --rm nivel2        # regras em escala + lote + confronto
docker compose run --rm nivel3        # agente via MCP + comparação de transportes
```

`outputs/` é montado como volume, então os resultados aparecem na máquina do host, não presos
dentro do container.

## Decisões da imagem

- **`python:3.12-slim`** em vez de `alpine`: pandas em Alpine precisa compilar (musl não tem
  wheels), o que multiplicaria o tempo de build por algo entre 5 e 10. `slim` já resolve o
  trade-off tamanho/tempo para o caso.
- **`requirements.txt` copiado antes do código**, em camada própria: alterar código não invalida
  o cache de instalação das dependências.
- **Versões fixadas** no `requirements.txt`. Não é preciosismo: a API do pacote `mcp` mudou entre
  1.x e 2.x **durante este desenvolvimento** (`MCPServer`/`@server.tool` substituíram
  `Server`/`@server.list_tools`), e o Nível 3 quebraria com um range aberto.
- **Sem `CMD` "esperto"**: cada nível é um comando diferente, e o `docker-compose.yml` documenta
  qual é qual. O default abre o Jupyter, que é o entregável do Nível 1.
- **`.env` no `.dockerignore`**, junto com `.venv/` e `.git/`. Chave em imagem é chave vazada —
  a imagem pode ser publicada, inspecionada por camada, ou ficar em cache de registry.

## O que isto não resolve

- **Pareceres não reproduzíveis** — é limitação do LLM, não do empacotamento. A correção proposta
  (persistir o parecer com o hash da entrada, tratando-o como artefato imutável) está em
  [`DECISOES.md`](DECISOES.md#reprodutibilidade-do-parecer).
- **Dependência de serviço externo** — sem rede ou com a cota do Groq esgotada, os níveis 2 e 3
  não rodam, container ou não. Rodar o modelo localmente (Ollama) eliminaria isso, ao custo de
  qualidade em *function calling*, que é justamente o que o Nível 2 exercita.
- **Rate limit** — 8.000 tokens/minuto no free tier. O container não muda a cota; o retry com
  backoff em `agente.py` é o que evita que o lote quebre.
