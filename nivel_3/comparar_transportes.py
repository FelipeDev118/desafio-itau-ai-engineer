"""Nivel 3 - Trilha B: valida a troca de transporte (import direto -> MCP).

A pergunta que este script responde: trocar o acesso as ferramentas de import direto
para o protocolo MCP mudou o COMPORTAMENTO do agente, ou so o caminho pelo qual ele
alcanca os mesmos dados?

Importante: o criterio ingenuo seria "os pareceres tem que sair identicos". Ele NAO se
sustenta - o LLM e nao-deterministico mesmo com temperature=0.2, entao divergencia de
texto e de nivel_risco entre as duas execucoes e esperada e nao prova nada sobre o MCP.
Por isso comparamos o que de fato deveria ser invariante sob troca de transporte:

1. as MESMAS ferramentas sao descobertas e chamadas (mesmo vocabulario de nomes);
2. as ferramentas retornam os MESMOS dados para os mesmos argumentos (verificado
   chamando as duas vias com os mesmos parametros e comparando o payload);
3. a ordem de grandeza de tokens/latencia se mantem (nenhum overhead estrutural).

O item 2 e o teste que realmente importa: se o payload bate, qualquer diferenca de
parecer vem do modelo, nao do transporte.

Rodar:
    python nivel_3/comparar_transportes.py
"""
import asyncio
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "nivel_2"))

import pandas as pd
from mcp import ClientSession
from mcp.client.stdio import stdio_client

import tools as tools_diretas
from agente_mcp import PARAMETROS_SERVIDOR

CASOS = [
    ("historico_cliente", {"cliente_id": "CLI-014"}),
    ("historico_cliente", {"cliente_id": "CLI-029"}),
    ("perfil_canal", {"cliente_id": "CLI-014"}),
    ("perfil_canal", {"cliente_id": "CLI-023"}),
    ("operacoes_do_dia", {"cliente_id": "CLI-014", "data": "2026-05-26"}),
    ("operacoes_do_dia", {"cliente_id": "CLI-017", "data": "2026-03-08"}),
]


async def comparar_payloads() -> pd.DataFrame:
    """Chama cada ferramenta pelas duas vias com os mesmos argumentos e compara o retorno."""
    linhas = []
    async with stdio_client(PARAMETROS_SERVIDOR) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            publicadas = sorted(f.name for f in (await session.list_tools()).tools)

            for nome, args in CASOS:
                direto = tools_diretas.TOOLS_SPEC[nome]["fn"](**args)
                via_mcp_raw = await session.call_tool(nome, args)
                via_mcp = json.loads(via_mcp_raw.content[0].text)

                # normaliza via json para comparar tipos serializados dos dois lados
                direto_norm = json.loads(json.dumps(direto, default=str))
                linhas.append(
                    {
                        "ferramenta": nome,
                        "argumentos": json.dumps(args, ensure_ascii=False),
                        "payload_identico": direto_norm == via_mcp,
                    }
                )

    return pd.DataFrame(linhas), publicadas


def comparar_execucoes() -> dict:
    """Compara os dois lotes ja executados (import direto vs MCP)."""
    with open(RAIZ / "outputs" / "pareceres_lote.json", encoding="utf-8") as f:
        direto = {r["cliente_id"]: r for r in json.load(f)}
    with open(RAIZ / "outputs" / "pareceres_lote_mcp.json", encoding="utf-8") as f:
        via_mcp = {r["cliente_id"]: r for r in json.load(f)}

    nomes_direto = {t["tool"] for r in direto.values() for t in r["tools_chamadas"]}
    nomes_mcp = {t["tool"] for r in via_mcp.values() for t in r["tools_chamadas"]}

    linhas = []
    for cid in direto:
        d, m = direto[cid], via_mcp[cid]
        linhas.append(
            {
                "cliente_id": cid,
                "risco_direto": (d["parecer"] or {}).get("nivel_risco"),
                "risco_mcp": (m["parecer"] or {}).get("nivel_risco"),
                "tools_direto": len(d["tools_chamadas"]),
                "tools_mcp": len(m["tools_chamadas"]),
                "tokens_direto": d["tokens_total"],
                "tokens_mcp": m["tokens_total"],
            }
        )
    df = pd.DataFrame(linhas)
    return {
        "df": df,
        "vocabulario_identico": nomes_direto == nomes_mcp,
        "nomes_direto": sorted(nomes_direto),
        "nomes_mcp": sorted(nomes_mcp),
    }


async def main():
    payloads, publicadas = await comparar_payloads()
    print("=== 1. Ferramentas publicadas pelo servidor MCP ===")
    print(publicadas)

    print("\n=== 2. Payload identico entre import direto e MCP? ===")
    print(payloads.to_string(index=False))
    todos_iguais = bool(payloads["payload_identico"].all())
    print(f"\nTodos os payloads identicos: {todos_iguais}")

    exec_cmp = comparar_execucoes()
    df = exec_cmp["df"]
    print("\n=== 3. Execucao em lote: direto vs MCP ===")
    print(df.to_string(index=False))

    iguais = int((df["risco_direto"] == df["risco_mcp"]).sum())
    print(f"\nMesmo nivel_risco: {iguais}/{len(df)} "
          "(divergencia aqui e esperada - o LLM nao e deterministico)")
    print(f"Vocabulario de ferramentas identico: {exec_cmp['vocabulario_identico']}")
    print(f"Tokens totais  direto: {df['tokens_direto'].sum()} | mcp: {df['tokens_mcp'].sum()}")
    print(f"Pareceres sem parse   direto: {df['risco_direto'].isna().sum()} | "
          f"mcp: {df['risco_mcp'].isna().sum()}")

    destino = RAIZ / "outputs" / "comparacao_transportes.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(
            {
                "ferramentas_publicadas_mcp": publicadas,
                "payloads_identicos": todos_iguais,
                "casos_payload": payloads.to_dict(orient="records"),
                "vocabulario_ferramentas_identico": exec_cmp["vocabulario_identico"],
                "mesmo_nivel_risco": f"{iguais}/{len(df)}",
                "tokens_direto": int(df["tokens_direto"].sum()),
                "tokens_mcp": int(df["tokens_mcp"].sum()),
                "comparacao_por_cliente": df.to_dict(orient="records"),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nSalvo em {destino}")


if __name__ == "__main__":
    asyncio.run(main())
