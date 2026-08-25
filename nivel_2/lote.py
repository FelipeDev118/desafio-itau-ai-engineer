"""Parte C - executa o agente sobre os 10 clientes mais sinalizados e salva os
resultados em outputs/, com analise de custo/latencia em pandas.

Nao faz parte da lista de arquivos obrigatorios do enunciado (tools.py, agente.py,
confronto.py), mas o enunciado pede explicitamente a execucao em lote e o registro
de custo/latencia (Parte C, itens 1-3) - colocamos aqui para nao inflar agente.py
com logica de orquestracao de lote. Ver DECISOES.md.
"""
import json
import time
from pathlib import Path

import pandas as pd

from agente import rodar_agente
from dados import aplicar_regras, carregar_e_limpar, ranking_clientes_sinalizados
from observabilidade import Coletor

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


def main():
    df, _ = carregar_e_limpar()
    df = aplicar_regras(df)
    top10 = ranking_clientes_sinalizados(df, top_n=10)

    coletor = Coletor()
    resultados = []
    for _, row in top10.iterrows():
        cliente_id = row["cliente_id"]
        flags = {
            "flag_fracionamento": bool(row["sinalizacoes_fracionamento"]),
            "flag_valor_atipico": bool(row["sinalizacoes_valor_atipico"] > 0),
        }
        print(f"Processando {cliente_id}...")
        resultado = rodar_agente(cliente_id, flags, coletor=coletor)
        resultado["flags_deterministicas"] = flags
        resultado["volume_total_brl"] = round(float(row["volume_total_brl"]), 2)
        resultado["total_sinalizacoes_deterministicas"] = int(row["total_sinalizacoes"])
        resultados.append(resultado)
        time.sleep(8)  # respeitar rate limit de tokens/minuto do free tier

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
    print(f"\nSalvo em {OUTPUTS_DIR}: pareceres_lote.json, metricas_lote.csv, "
          "chamadas_llm.csv, custo_resumo.json")


if __name__ == "__main__":
    main()
