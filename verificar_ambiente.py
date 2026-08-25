"""Verificacao de reprodutibilidade - roda sem chave de API e sem chamar LLM.

Por que este script existe: o problema do "so funciona na minha maquina" nao se resolve
provando que o codigo *executa* em outro lugar, e sim provando que ele chega ao *mesmo
resultado*. Aqui isso e possivel para a camada deterministica - e so para ela.

O que e verificavel:
  - limpeza dos dados (duplicatas, datas nulas, conversao de moeda)
  - Regra 1 e Regra 2
  - ranking dos 10 clientes mais sinalizados

O que NAO e verificavel, e o motivo importa: os pareceres do LLM nao sao reproduziveis
nem na mesma maquina. O mesmo agente atribuiu nivel_risco diferente para 4 de 10 clientes
entre duas execucoes (ver docs/ARQUITETURA.md). Entao comparar parecer contra parecer
seria um teste que falha por motivo errado. Este script compara o que deve ser identico
em qualquer maquina, e reporta o resto como informativo.

Rodar:
    python verificar_ambiente.py          # local
    docker compose run --rm verificar     # no container
"""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "nivel_2"))

import pandas as pd

from dados import aplicar_regras, carregar_e_limpar, ranking_clientes_sinalizados

# Valores obtidos na execucao que gerou os arquivos em outputs/. Se a mesma base produzir
# numeros diferentes em outra maquina, algo mudou no ambiente (versao de pandas, encoding,
# arredondamento) e o resto da entrega precisa ser lido com desconfianca.
ESPERADO = {
    "operacoes_brutas": 322,
    "duplicatas_removidas": 5,
    "datas_nulas": 7,
    "operacoes_usd": 7,
    "operacoes_apos_limpeza": 317,
    "clientes_fracionamento": 4,  # CLI-002, CLI-003, CLI-017, CLI-029
    "operacoes_atipicas": 21,     # distribuidas em 13 clientes
    "top10": [
        "CLI-014", "CLI-023", "CLI-028", "CLI-013", "CLI-005",
        "CLI-026", "CLI-001", "CLI-029", "CLI-017", "CLI-030",
    ],
}


def main() -> int:
    print(f"pandas {pd.__version__} | python {sys.version.split()[0]}\n")

    bruto = json.loads((RAIZ / "dados" / "dados_nivel_2.json").read_text(encoding="utf-8"))
    df_bruto = pd.DataFrame(bruto["operacoes"])

    df, taxa = carregar_e_limpar()
    df = aplicar_regras(df)
    top10 = ranking_clientes_sinalizados(df, top_n=10)

    obtido = {
        "operacoes_brutas": len(df_bruto),
        "duplicatas_removidas": int(df_bruto["id"].duplicated().sum()),
        "datas_nulas": int(df_bruto["data"].isna().sum()),
        "operacoes_usd": int((df_bruto["moeda"] == "USD").sum()),
        "operacoes_apos_limpeza": len(df),
        "clientes_fracionamento": int(df.loc[df["flag_fracionamento"], "cliente_id"].nunique()),
        "operacoes_atipicas": int(df["flag_valor_atipico"].sum()),
        "top10": top10["cliente_id"].tolist(),
    }

    print(f"{'verificacao':28} {'esperado':>12} {'obtido':>12}   ")
    falhas = []
    for chave, esperado in ESPERADO.items():
        if chave == "top10":
            continue
        ok = obtido[chave] == esperado
        if not ok:
            falhas.append(chave)
        print(f"{chave:28} {esperado:>12} {obtido[chave]:>12}   {'OK' if ok else 'FALHOU'}")

    ok_top = obtido["top10"] == ESPERADO["top10"]
    if not ok_top:
        falhas.append("top10")
    print(f"\ntop 10 clientes sinalizados: {'OK' if ok_top else 'FALHOU'}")
    print(f"  esperado: {ESPERADO['top10']}")
    print(f"  obtido:   {obtido['top10']}")
    print(f"\ntaxa de cambio lida do arquivo: {taxa}")

    # Informativo: nao entra no criterio de sucesso, porque parecer de LLM nao e reproduzivel.
    caminho_lote = RAIZ / "outputs" / "pareceres_lote.json"
    if caminho_lote.exists():
        lote = json.loads(caminho_lote.read_text(encoding="utf-8"))
        riscos = pd.Series(
            [(r.get("parecer") or {}).get("nivel_risco") for r in lote]
        ).value_counts().to_dict()
        print(f"\n[informativo] pareceres commitados em outputs/: {len(lote)} | "
              f"distribuicao de nivel_risco: {riscos}")
        print("  (nao verificado: resposta de LLM nao e reproduzivel entre execucoes)")

    if falhas:
        print(f"\nFALHOU em: {', '.join(falhas)}")
        print("A camada deterministica divergiu - investigar antes de confiar no resto.")
        return 1

    print("\nOK: a camada deterministica reproduz exatamente os numeros da entrega.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
