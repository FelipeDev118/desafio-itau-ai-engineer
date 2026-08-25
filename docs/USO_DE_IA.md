# Uso de IA

## Ferramentas usadas

- **Claude Code (Anthropic)** — usado como par de programação ao longo de todo o
  desenvolvimento, em sessão interativa no terminal.
- **Groq / `openai/gpt-oss-120b`** — este é o LLM *da solução* (não de desenvolvimento): é ele
  que produz os pareceres no Nível 1 Parte B e no agente do Nível 2.

## Para quê usei

- Estruturação inicial do repositório a partir do enunciado (pastas, `requirements.txt`,
  `.gitignore`, `.env.example`).
- Implementação do tratamento de dados e das duas regras determinísticas em pandas, discutindo
  antes cada decisão ambígua (como tratar data nula, se `R$ 20.000,00` exatos "atingem" o
  limite, como contar sinalizações no ranking).
- Desenho dos dois prompts do Nível 1 e do schema Pydantic de validação.
- Implementação do agente com function calling nativo, das três ferramentas e do script de
  confronto.
- Redação da documentação (`DECISOES.md`, este arquivo, `README.md`), a partir do que de fato
  foi executado — não de um plano hipotético.

## Onde a IA me levou (ou quase me levou) para o caminho errado

1. **Modelo sugerido que não existe mais.** A primeira tentativa de configuração usou
   `llama-3.3-70b-versatile` (modelo sugerido no próprio enunciado e o "default" que a IA
   assumiu). A chamada falhou com `404 model_not_found` — os modelos gratuitos do Groq mudaram.
   Só descobri porque testei a conexão com a API **antes** de escrever o resto do código, em vez
   de confiar no nome do modelo. Corrigi listando os modelos realmente disponíveis na conta
   (`client.models.list()`) e escolhendo `openai/gpt-oss-120b`. Lição: nome de modelo em
   documentação (ou na memória de um LLM) envelhece rápido; verificar contra a API é barato.

2. **Primeiro teste "silencioso" que parecia sucesso.** O primeiro teste de conexão retornou
   `content` vazio com `finish_reason: stop`, e a leitura apressada seria "a API não funciona".
   Na verdade o `gpt-oss` gasta tokens num campo `reasoning` antes de responder, e o
   `max_tokens=10` que eu tinha posto consumia tudo no raciocínio, sobrando zero para a
   resposta. Só apareceu ao inspecionar o objeto `message` inteiro. Isso mudou o desenho: todos
   os `max_tokens` do projeto foram dimensionados com folga por causa disso.

3. **A própria IA da solução alucinou dados — e isso virou conteúdo da entrega.** No Nível 1,
   o prompt v1 fez o modelo inventar um limite de "R$ 10.000" que não existe no enunciado. Em
   vez de esconder ou refazer o prompt até "dar certo", mantive a execução no notebook e
   documentei o erro na comparação v1 vs v2 — é a evidência mais concreta de por que cálculo
   deve ficar em pandas e o LLM só interpretar.

4. **Bug de tool calling que a IA não previu.** O agente quebrou em produção (`400
   tool_use_failed`) porque o modelo tentava chamar uma ferramenta fictícia chamada `JSON` para
   devolver a resposta final. Isso não estava em nenhum plano inicial — apareceu só ao rodar o
   lote de verdade, e exigiu ler o corpo do erro para descobrir que o conteúdo válido vinha em
   `failed_generation`. Rodar de verdade encontrou o que o planejamento não encontrou.

## Sobre autoria e entendimento

O código foi escrito em par com a IA, mas cada decisão ambígua do enunciado foi discutida e
decidida explicitamente antes da implementação, e as justificativas estão em `docs/DECISOES.md`.
Os achados mais interessantes desta entrega (a alucinação do prompt v1, o bug do `tool JSON`, o
padrão sistemático das 6 divergências no confronto, os caracteres invisíveis no parecer de
`CLI-028`) vieram de rodar e inspecionar os resultados, não de gerar código — e é isso que eu
levaria para a entrevista.
