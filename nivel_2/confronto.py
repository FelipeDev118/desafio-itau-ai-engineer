"""Parte D - confronta o nivel_risco atribuido pelo agente (LLM) com o que as regras
deterministicas apontariam para o mesmo cliente.

Criterio de correspondencia (ver DECISOES.md para a justificativa completa):
- fracionamento ativo, OU 2+ operacoes com valor atipico  -> regra espera "alto"
- exatamente 1 sinalizacao de valor atipico, sem fracionamento -> regra espera "medio"
(Não existe caso "nenhuma flag" aqui: só entram no confronto clientes que já foram
sinalizados por pelo menos uma regra - ver ranking_clientes_sinalizados em dados.py.)

O criterio usa a INTENSIDADE da sinalizacao (quantas operacoes atipicas), e nao apenas
"quantas regras distintas dispararam". Um criterio anterior - "ambas as flags -> alto" -
foi descartado porque, nesta base, nenhum cliente do top 10 dispara as duas regras ao
mesmo tempo: o ramo "alto" nunca seria exercido e todos os 10 casos esperariam "medio",
o que tornaria a taxa de concordancia uma metrica vazia. Ver DECISOES.md.

Concordancia = nivel_risco do agente == nivel_risco esperado pela regra.
"""
import json
from pathlib import Path

import pandas as pd

from dados import aplicar_regras, carregar_e_limpar, ranking_clientes_sinalizados

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"


def _normalizar_nivel(nivel: str | None) -> str | None:
    """O enunciado especifica os valores como baixo/medio/alto (com acento em 'medio').
    Aceitamos as duas grafias e normalizamos para a forma acentuada do enunciado, para
    que a comparacao nao dependa de o modelo ter acentuado ou nao."""
    if nivel is None:
        return None
    return {"medio": "médio"}.get(nivel.strip().lower(), nivel.strip().lower())


def nivel_risco_esperado(flag_fracionamento: bool, qtd_atipicas: int) -> str:
    if flag_fracionamento or qtd_atipicas >= 2:
        return "alto"
    return "médio"


def main():
    with open(OUTPUTS_DIR / "pareceres_lote.json", encoding="utf-8") as f:
        resultados = json.load(f)

    # qtd de operacoes atipicas por cliente vem das regras deterministicas, nao do parecer
    df, _ = carregar_e_limpar()
    df = aplicar_regras(df)
    ranking = ranking_clientes_sinalizados(df, top_n=10).set_index("cliente_id")

    linhas = []
    for r in resultados:
        flags = r["flags_deterministicas"]
        cliente_id = r["cliente_id"]
        qtd_atipicas = int(ranking.loc[cliente_id, "sinalizacoes_valor_atipico"])
        esperado = nivel_risco_esperado(flags["flag_fracionamento"], qtd_atipicas)
        obtido = _normalizar_nivel((r["parecer"] or {}).get("nivel_risco"))
        linhas.append(
            {
                "cliente_id": cliente_id,
                "flag_fracionamento": flags["flag_fracionamento"],
                "qtd_operacoes_atipicas": qtd_atipicas,
                "nivel_risco_esperado_regra": esperado,
                # "sem_parecer" (nao None) para nao virar NaN silencioso num DataFrame
                # e para deixar explicito, no CSV, que o agente nao produziu resposta -
                # ver erro_parsing para o motivo.
                "nivel_risco_agente": obtido or "sem_parecer",
                "concorda": obtido == esperado,
                "erro_parsing": r.get("erro_parsing"),
                "tipologia_suspeita": (r["parecer"] or {}).get("tipologia_suspeita"),
                "justificativa": (r["parecer"] or {}).get("justificativa"),
            }
        )

    confronto = pd.DataFrame(linhas)

    # "sem parecer" (erro_parsing preenchido) e "parecer valido mas discordante" sao
    # falhas de natureza diferente - taxa_concordancia misturando as duas mede menos
    # do que parece. Separamos aqui para o numero nao mentir por omissao.
    sem_parecer = confronto["erro_parsing"].notna()
    avaliaveis = confronto[~sem_parecer]
    taxa_concordancia = avaliaveis["concorda"].mean() if len(avaliaveis) else float("nan")
    taxa_resposta_valida = (~sem_parecer).mean()

    print(confronto[["cliente_id", "flag_fracionamento", "qtd_operacoes_atipicas",
                      "nivel_risco_esperado_regra", "nivel_risco_agente", "concorda"]])
    print(f"\nRespostas validas: {(~sem_parecer).sum()}/{len(confronto)} "
          f"({taxa_resposta_valida:.0%})")
    if len(avaliaveis):
        print(f"Taxa de concordancia (entre as respostas validas): {taxa_concordancia:.0%} "
              f"({int(avaliaveis['concorda'].sum())}/{len(avaliaveis)})")

    divergentes = avaliaveis[~avaliaveis["concorda"]]
    print(f"\n--- Divergencias qualitativas ({len(divergentes)}) ---")
    for _, row in divergentes.iterrows():
        print(f"\n{row['cliente_id']}: regra esperava '{row['nivel_risco_esperado_regra']}', "
              f"agente deu '{row['nivel_risco_agente']}'")
        print(f"  justificativa do agente: {row['justificativa']}")

    falhas = confronto[sem_parecer]
    if len(falhas):
        print(f"\n--- Sem parecer valido ({len(falhas)}) ---")
        for _, row in falhas.iterrows():
            print(f"{row['cliente_id']}: {row['erro_parsing']}")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    confronto.to_csv(OUTPUTS_DIR / "confronto_regra_vs_agente.csv", index=False)
    with open(OUTPUTS_DIR / "confronto_resumo.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "total_clientes": len(confronto),
                "respostas_validas": int((~sem_parecer).sum()),
                "sem_parecer": int(sem_parecer.sum()),
                "taxa_concordancia_entre_validas": (
                    round(float(taxa_concordancia), 4) if len(avaliaveis) else None
                ),
                "concordantes": int(avaliaveis["concorda"].sum()) if len(avaliaveis) else 0,
                "divergentes_qualitativas": int((~avaliaveis["concorda"]).sum()) if len(avaliaveis) else 0,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"\nSalvo em {OUTPUTS_DIR / 'confronto_regra_vs_agente.csv'}")


if __name__ == "__main__":
    main()
