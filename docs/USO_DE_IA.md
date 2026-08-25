# Uso de IA

## Ferramentas

- **Claude Code (Anthropic)** — par de programação durante todo o desenvolvimento: estruturação
  do repositório, implementação em pandas, agente, servidor MCP e redação dos documentos.
- **Groq / `openai/gpt-oss-120b`** — não é ferramenta de desenvolvimento, é o LLM **da solução**:
  produz os pareceres do Nível 1 Parte B e do agente.

Cada decisão ambígua do enunciado foi discutida antes de implementar; as justificativas estão em
[`DECISOES.md`](DECISOES.md).

## Onde a IA me levou para o caminho errado

**1. Documentação convincente que os próprios resultados desmentiam — o pior dos casos.**
O `README.md` e o `DECISOES.md` afirmavam, como prova de que o agente decidia bem, que ele "não
chama `operacoes_do_dia` para clientes sem fracionamento". Rodei uma auditoria conferindo cada
afirmação contra `outputs/pareceres_lote.json` e era **o oposto**: 7 clientes sem a flag chamaram
a ferramenta, e o único com a flag não chamou. A frase tinha sido escrita a partir de uma
execução isolada e generalizada sem verificação.

Foi o erro mais sério porque não era código quebrado — era uma conclusão plausível e bem escrita
que eu quase entreguei. Investigar a causa revelou um defeito real de desenho meu (o prompt
informa a flag sem informar a data que a disparou), hoje documentado. Depois disso passei a
conferir toda afirmação de resultado contra os arquivos de saída antes de aceitá-la.

**2. Nome de modelo desatualizado.** A configuração inicial usou `llama-3.3-70b-versatile` — o
modelo sugerido no enunciado e o que a IA assumiu por padrão. Retornou `404 model_not_found`: os
modelos gratuitos do Groq mudaram. Só descobri porque testei a conexão antes de escrever o resto.
Passei a validar contra `client.models.list()`.

**3. Critério de validação que não se sustentava.** O plano inicial para validar a troca de
transporte no Nível 3 era "os pareceres têm que sair idênticos". Ao executar, deu 6/10 — e o
motivo não era o MCP, era o LLM não ser determinístico. O critério estava errado desde o começo.
Troquei por comparar o **payload das ferramentas** (o que de fato deve ser invariante): 6/6
idênticos. O erro rendeu o achado de auditabilidade descrito em [`ARQUITETURA.md`](ARQUITETURA.md).

**4. Quase documentei uma conclusão a partir de uma única execução.** Na primeira execução do
notebook, o prompt v1 alucinou um limite de reporte de "≈ R$ 10.000" (o real é R$ 20.000,00), e
eu escrevi a comparação de prompts tratando isso como característica fixa do v1. Ao reexecutar o
notebook para adicionar a validação da Regra 2, **o mesmo prompt não repetiu o erro**. A
conclusão correta não era "v1 alucina", era "v1 é **instável**" — por ser subespecificado, deixa
o modelo preencher a lacuna do limite com conhecimento genérico, às vezes certo, às vezes não. A
execução original está preservada no commit `9a59dd8`, e o notebook hoje compara as duas.

**5. O LLM da solução alucina de forma persuasiva.** No confronto, o parecer de `CLI-005`
fundamenta o risco numa operação de R$ 409,16 — abaixo da mediana do cliente, portanto não é a
operação atípica — e erra o ano da data. O texto é bem escrito e soa técnico. É a razão pela qual
todo cálculo ficou em pandas: a camada determinística é o que permite auditar o modelo.

O padrão dos quatro é o mesmo: a IA erra de forma plausível, e o que pegou os erros foi executar
e conferir contra os dados, não revisar o texto.
