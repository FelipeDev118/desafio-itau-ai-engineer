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

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


def main():
    df, _ = carregar_e_limpar()
    df = aplicar_regras(df)
    top10 = ranking_clientes_sinalizados(df, top_n=10)

    resultados = []
    for _, row in top10.iterrows():
        cliente_id = row["cliente_id"]
        flags = {
            "flag_fracionamento": bool(row["sinalizacoes_fracionamento"]),
            "flag_valor_atipico": bool(row["sinalizacoes_valor_atipico"] > 0),
        }
        print(f"Processando {cliente_id}...")
        resultado = rodar_agente(cliente_id, flags)
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

    print("\n--- Resumo de custo/latencia ---")
    print(f"Tokens totais consumidos: {metricas['tokens_total'].sum()}")
    print(f"Tokens medios por cliente: {metricas['tokens_total'].mean():.0f}")
    print(f"Latencia media (s): {metricas['latencia_s'].mean():.2f}")
    print(f"Latencia total (s): {metricas['latencia_s'].sum():.2f}")
    print(f"Respostas malformadas: {metricas['erro_parsing'].notna().sum()} / {len(metricas)}")
    print(f"\nSalvo em {OUTPUTS_DIR / 'pareceres_lote.json'} e {OUTPUTS_DIR / 'metricas_lote.csv'}")


if __name__ == "__main__":
    main()
