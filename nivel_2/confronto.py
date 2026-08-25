"""Parte D - confronta o nivel_risco atribuido pelo agente (LLM) com o que as regras
deterministicas apontariam para o mesmo cliente.

Criterio de correspondencia (ver DECISOES.md para a justificativa completa):
- cliente com AMBAS as flags ativas (fracionamento E valor atipico)  -> regra espera "alto"
- cliente com APENAS UMA flag ativa                                  -> regra espera "medio"
(Não existe caso "nenhuma flag" aqui: só entram no confronto clientes que já foram
sinalizados por pelo menos uma regra - ver ranking_clientes_sinalizados em dados.py.)

Concordancia = nivel_risco do agente == nivel_risco esperado pela regra.
"""
import json
from pathlib import Path

import pandas as pd

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


def nivel_risco_esperado(flags: dict) -> str:
    if flags["flag_fracionamento"] and flags["flag_valor_atipico"]:
        return "alto"
    return "medio"


def main():
    with open(OUTPUTS_DIR / "pareceres_lote.json", encoding="utf-8") as f:
        resultados = json.load(f)

    linhas = []
    for r in resultados:
        flags = r["flags_deterministicas"]
        esperado = nivel_risco_esperado(flags)
        obtido = (r["parecer"] or {}).get("nivel_risco")
        linhas.append(
            {
                "cliente_id": r["cliente_id"],
                "flag_fracionamento": flags["flag_fracionamento"],
                "flag_valor_atipico": flags["flag_valor_atipico"],
                "nivel_risco_esperado_regra": esperado,
                "nivel_risco_agente": obtido,
                "concorda": obtido == esperado,
                "tipologia_suspeita": (r["parecer"] or {}).get("tipologia_suspeita"),
                "justificativa": (r["parecer"] or {}).get("justificativa"),
            }
        )

    confronto = pd.DataFrame(linhas)
    taxa_concordancia = confronto["concorda"].mean()

    print(confronto[["cliente_id", "flag_fracionamento", "flag_valor_atipico",
                      "nivel_risco_esperado_regra", "nivel_risco_agente", "concorda"]])
    print(f"\nTaxa de concordancia: {taxa_concordancia:.0%} ({confronto['concorda'].sum()}/{len(confronto)})")

    divergentes = confronto[~confronto["concorda"]]
    print(f"\n--- Divergencias ({len(divergentes)}) ---")
    for _, row in divergentes.iterrows():
        print(f"\n{row['cliente_id']}: regra esperava '{row['nivel_risco_esperado_regra']}', "
              f"agente deu '{row['nivel_risco_agente']}'")
        print(f"  justificativa do agente: {row['justificativa']}")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    confronto.to_csv(OUTPUTS_DIR / "confronto_regra_vs_agente.csv", index=False)
    with open(OUTPUTS_DIR / "confronto_resumo.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "taxa_concordancia": round(float(taxa_concordancia), 4),
                "total_clientes": len(confronto),
                "concordantes": int(confronto["concorda"].sum()),
                "divergentes": int((~confronto["concorda"]).sum()),
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nSalvo em {OUTPUTS_DIR / 'confronto_regra_vs_agente.csv'}")


if __name__ == "__main__":
    main()
