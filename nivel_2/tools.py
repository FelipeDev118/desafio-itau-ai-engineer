"""Ferramentas que o agente pode chamar para investigar um cliente.
Cada função consulta a base já tratada (ver dados.py) — cálculo puro, sem LLM."""
import pandas as pd

from dados import aplicar_regras, carregar_e_limpar


def _df():
    df, _ = carregar_e_limpar()
    return aplicar_regras(df)


def historico_cliente(cliente_id: str) -> dict:
    """Resumo agregado das operações do cliente: volume, contagem, período,
    canais/tipos mais usados e se há flags determinísticas ativas."""
    df = _df()
    sub = df[df["cliente_id"] == cliente_id]
    if sub.empty:
        return {"cliente_id": cliente_id, "erro": "cliente nao encontrado"}

    return {
        "cliente_id": cliente_id,
        "qtd_operacoes": int(len(sub)),
        "volume_total_brl": round(float(sub["valor_brl"].sum()), 2),
        "valor_medio_brl": round(float(sub["valor_brl"].mean()), 2),
        "valor_mediano_brl": round(float(sub["valor_brl"].median()), 2),
        "data_min": sub["data"].min().strftime("%Y-%m-%d") if sub["data"].notna().any() else None,
        "data_max": sub["data"].max().strftime("%Y-%m-%d") if sub["data"].notna().any() else None,
        "tipos_mais_comuns": sub["tipo"].value_counts().head(3).to_dict(),
        "contrapartes_mais_frequentes": sub["contraparte"].value_counts().head(3).to_dict(),
        "flag_fracionamento": bool(sub["flag_fracionamento"].any()),
        "flag_valor_atipico": bool(sub["flag_valor_atipico"].any()),
    }


def operacoes_do_dia(cliente_id: str, data: str) -> dict:
    """Recorte das operações de um cliente em uma data específica (YYYY-MM-DD)."""
    df = _df()
    alvo = pd.to_datetime(data)
    sub = df[(df["cliente_id"] == cliente_id) & (df["data"] == alvo)]
    ops = sub[["id", "valor_brl", "canal", "tipo", "contraparte"]].to_dict(orient="records")
    return {
        "cliente_id": cliente_id,
        "data": data,
        "qtd_operacoes": len(ops),
        "soma_valor_brl": round(float(sub["valor_brl"].sum()), 2) if not sub.empty else 0.0,
        "operacoes": ops,
    }


def perfil_canal(cliente_id: str) -> dict:
    """Distribuição de uso de canais do cliente (contagem e volume por canal)."""
    df = _df()
    sub = df[df["cliente_id"] == cliente_id]
    if sub.empty:
        return {"cliente_id": cliente_id, "erro": "cliente nao encontrado"}

    por_canal = sub.groupby("canal").agg(
        qtd=("id", "count"), volume_brl=("valor_brl", "sum")
    )
    return {
        "cliente_id": cliente_id,
        "distribuicao": {
            canal: {"qtd": int(row.qtd), "volume_brl": round(float(row.volume_brl), 2)}
            for canal, row in por_canal.iterrows()
        },
        "canal_predominante": por_canal["qtd"].idxmax() if not por_canal.empty else None,
    }


TOOLS_SPEC = {
    "historico_cliente": {
        "fn": historico_cliente,
        "descricao": "Resumo agregado das operacoes do cliente (volume, contagem, flags).",
        "parametros": ["cliente_id"],
    },
    "operacoes_do_dia": {
        "fn": operacoes_do_dia,
        "descricao": "Detalhe das operacoes do cliente em uma data especifica.",
        "parametros": ["cliente_id", "data"],
    },
    "perfil_canal": {
        "fn": perfil_canal,
        "descricao": "Distribuicao de uso de canais do cliente.",
        "parametros": ["cliente_id"],
    },
}


if __name__ == "__main__":
    print(historico_cliente("CLI-014"))
    print(perfil_canal("CLI-014"))
