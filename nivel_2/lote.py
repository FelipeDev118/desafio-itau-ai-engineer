"""Parte C - executa o agente sobre os clientes da base e salva os resultados em
outputs/, com analise de custo/latencia em pandas.

Nao faz parte da lista de arquivos obrigatorios do enunciado (tools.py, agente.py,
confronto.py), mas o enunciado pede explicitamente a execucao em lote e o registro
de custo/latencia (Parte C, itens 1-3) - colocamos aqui para nao inflar agente.py
com logica de orquestracao de lote. Ver DECISOES.md.

Por padrao cobre os 30 clientes da base, nao so os sinalizados pelas regras
deterministicas - existe para medir o falso negativo que nenhuma metrica media antes:
um cliente sem flag ainda seria marcado como risco pelo agente? (ver DECISOES.md,
"Cobrir os 30 clientes"). Passe `todos=False` para voltar ao comportamento antigo
(so os 10 mais sinalizados) - mais rapido, util para iterar em desenvolvimento.
"""
import json
import time
from pathlib import Path

import pandas as pd

from agente import rodar_agente
from cache_parecer import CacheParecer
from dados import (
    aplicar_regras,
    carregar_e_limpar,
    montar_flags,
    ranking_clientes_sinalizados,
    todos_os_clientes,
)
from observabilidade import Coletor
from verificacao_aderencia import verificar

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


def main(usar_cache: bool = True, todos: bool = True, top_n: int = 10):
    df, _ = carregar_e_limpar()
    df = aplicar_regras(df)
    clientes = todos_os_clientes(df) if todos else ranking_clientes_sinalizados(df, top_n=top_n)

    coletor = Coletor()
    cache = CacheParecer() if usar_cache else None
    cache_hits = 0
    resultados = []
    for _, row in clientes.iterrows():
        cliente_id = row["cliente_id"]
        flags = montar_flags(df, row)
        resultado = rodar_agente(cliente_id, flags, coletor=coletor, cache=cache)
        if resultado["cache_hit"]:
            cache_hits += 1
            print(f"Processando {cliente_id}... (cache hit, sem chamada de API)")
        else:
            print(f"Processando {cliente_id}...")
        resultado["flags_deterministicas"] = flags
        resultado["volume_total_brl"] = round(float(row["volume_total_brl"]), 2)
        resultado["total_sinalizacoes_deterministicas"] = int(row["total_sinalizacoes"])

        # Grounding check: o parecer cita numeros que existem de fato na base deste
        # cliente? Roda depois do cache de proposito - um parecer cacheado tambem
        # precisa ser auditado, e a verificacao e barata (sem LLM).
        if resultado.get("parecer"):
            aderencia = verificar(cliente_id, resultado["parecer"]["justificativa"], df)
            resultado["aderencia"] = {
                "fundamentado": aderencia.fundamentado,
                "motivo": aderencia.motivo,
                "valores_confirmados": aderencia.valores_confirmados,
                "valores_nao_encontrados": aderencia.valores_nao_encontrados,
                "atipicos_incorretos": aderencia.atipicos_incorretos,
            }
        else:
            resultado["aderencia"] = {
                "fundamentado": False,
                "motivo": "sem parecer para verificar",
            }

        resultados.append(resultado)
        if not resultado["cache_hit"]:
            time.sleep(8)  # respeitar rate limit de tokens/minuto do free tier - so entre chamadas reais

    if usar_cache:
        print(f"\nCache: {cache_hits}/{len(resultados)} clientes reaproveitados "
              f"({len(cache)} entradas no total)")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    with open(OUTPUTS_DIR / "pareceres_lote.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)

    metricas = pd.DataFrame(
        [
            {
                "cliente_id": r["cliente_id"],
                "nivel_risco": (r["parecer"] or {}).get("nivel_risco"),
                "erro_parsing": r["erro_parsing"],
                "qtd_tools_chamadas": len(r["tools_chamadas"]),
                "tokens_total": r["tokens_total"],
                "latencia_s": r["latencia_s"],
            }
            for r in resultados
        ]
    )
    metricas.to_csv(OUTPUTS_DIR / "metricas_lote.csv", index=False)

    # --- Observabilidade por CHAMADA de API (nao por cliente) ---
    chamadas = coletor.para_dataframe()
    chamadas.to_csv(OUTPUTS_DIR / "chamadas_llm.csv", index=False)

    resumo = coletor.resumo()
    with open(OUTPUTS_DIR / "custo_resumo.json", "w", encoding="utf-8") as f:
        json.dump(resumo, f, indent=2, ensure_ascii=False)

    print("\n--- Custo e latencia por chamada de API ---")
    print(f"Chamadas de API: {resumo['chamadas_api']} para {resumo['clientes']} clientes "
          f"({resumo['chamadas_por_cliente']} por cliente)")
    print(f"Tokens  entrada: {resumo['tokens_entrada']:>7} | saida: {resumo['tokens_saida']:>7} "
          f"| total: {resumo['tokens_total']:>7}")
    print(f"Custo equivalente total: US$ {resumo['custo_total_usd']:.6f} "
          f"(free tier: US$ 0,00 pago)")
    print(f"Custo medio por cliente: US$ {resumo['custo_medio_por_cliente_usd']:.6f}")
    print(f"Latencia por chamada - media: {resumo['latencia_media_por_chamada_s']}s | "
          f"p95: {resumo['latencia_p95_s']}s")

    if chamadas.empty:
        print("\n(nenhuma chamada de API nesta execucao - todos os clientes vieram do cache)")
    else:
        print("\nPor tipo de turno (onde o custo esta concentrado):")
        print(chamadas.groupby("tipo_turno").agg(
            chamadas=("turno", "count"),
            tokens_entrada=("tokens_entrada", "sum"),
            tokens_saida=("tokens_saida", "sum"),
            custo_usd=("custo_usd", "sum"),
            latencia_media_s=("latencia_s", "mean"),
        ).round(6).to_string())

    print(f"\nProjecao 30 clientes:      US$ {resumo['projecao_30_clientes_usd']:.6f}")
    print(f"Projecao 10.000 clientes:  US$ {resumo['projecao_10k_clientes_usd']:.2f}")
    print(f"Respostas malformadas: {metricas['erro_parsing'].notna().sum()} / {len(metricas)}")

    # --- Aderencia do parecer aos dados (grounding check, sem LLM) ---
    aderencia_df = pd.DataFrame([
        {"cliente_id": r["cliente_id"], **r["aderencia"]} for r in resultados
    ])
    aderencia_df.to_csv(OUTPUTS_DIR / "aderencia_pareceres.csv", index=False)

    com_parecer = aderencia_df[aderencia_df["motivo"] != "sem parecer para verificar"]
    n_fund = int(com_parecer["fundamentado"].sum()) if len(com_parecer) else 0
    print(f"\n--- Aderencia aos dados ---")
    print(f"Pareceres fundamentados: {n_fund}/{len(com_parecer)} "
          "(valores citados conferem com a base do cliente)")
    nao_fund = com_parecer[~com_parecer["fundamentado"]]
    for _, row in nao_fund.iterrows():
        print(f"  {row['cliente_id']}: {row['motivo']}")
    print(f"\nSalvo em {OUTPUTS_DIR}: pareceres_lote.json, metricas_lote.csv, "
          "chamadas_llm.csv, custo_resumo.json")


if __name__ == "__main__":
    main()
