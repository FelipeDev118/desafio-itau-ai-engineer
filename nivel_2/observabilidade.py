"""Observabilidade de custo e latencia por CHAMADA de API.

Motivacao: o enunciado (Nivel 2, Parte C, item 3) pede custo e latencia "de cada chamada".
Uma primeira versao registrava por cliente, agregando os turnos - o que esconde justamente
a informacao acionavel, porque um cliente e varias chamadas de API: uma ou mais para o
modelo decidir quais ferramentas usar, e uma final para redigir o parecer. Sem separar,
nao da para saber qual turno custa caro nem onde otimizar o prompt.

Aqui cada chamada vira uma linha, com o turno e o tipo de turno identificados.

Sobre o custo: no free tier do Groq o valor pago e ZERO. O que este modulo calcula e o
custo EQUIVALENTE - quanto essa execucao custaria na tabela do provedor. E o numero que
importa para responder "quanto custaria rodar isso para os 30 clientes, ou para a base
inteira de um banco", que e a pergunta de engenharia por tras do item.
"""
import os
from dataclasses import asdict, dataclass, field

import pandas as pd

# Precos em USD por 1 milhao de tokens.
# Fonte: https://console.groq.com/docs/model/openai/gpt-oss-120b (consultado em 2026-08-25).
# Preco de tabela envelhece: por isso fica isolado aqui e pode ser sobrescrito por variavel
# de ambiente, em vez de espalhado como numero magico pelo codigo.
PRECOS_USD_POR_MILHAO = {
    "openai/gpt-oss-120b": {"entrada": 0.15, "saida": 0.60},
    "openai/gpt-oss-20b": {"entrada": 0.10, "saida": 0.50},
}

_PADRAO = {"entrada": 0.15, "saida": 0.60}


def preco_do_modelo(modelo: str) -> dict:
    if os.environ.get("PRECO_ENTRADA_USD_MILHAO") and os.environ.get("PRECO_SAIDA_USD_MILHAO"):
        return {
            "entrada": float(os.environ["PRECO_ENTRADA_USD_MILHAO"]),
            "saida": float(os.environ["PRECO_SAIDA_USD_MILHAO"]),
        }
    return PRECOS_USD_POR_MILHAO.get(modelo, _PADRAO)


def calcular_custo_usd(modelo: str, tokens_entrada: int, tokens_saida: int) -> float:
    p = preco_do_modelo(modelo)
    return (tokens_entrada / 1_000_000) * p["entrada"] + (tokens_saida / 1_000_000) * p["saida"]


@dataclass
class ChamadaLLM:
    """Uma unica requisicao a API do modelo."""
    cliente_id: str
    turno: int
    tipo_turno: str  # "decisao_ferramenta" | "resposta_final"
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    tokens_total: int
    latencia_s: float
    custo_usd: float
    transporte: str = "import_direto"  # ou "mcp"
    tentativas_rate_limit: int = 0


@dataclass
class Coletor:
    """Acumula as chamadas de uma execucao. Instanciado por lote, nao global, para que
    duas execucoes na mesma sessao nao misturem registros."""
    chamadas: list[ChamadaLLM] = field(default_factory=list)

    def registrar(self, **kwargs) -> ChamadaLLM:
        chamada = ChamadaLLM(**kwargs)
        self.chamadas.append(chamada)
        return chamada

    def para_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(c) for c in self.chamadas])

    def resumo(self) -> dict:
        """Agregacoes em pandas - o enunciado pede explicitamente 'analise os totais com pandas'.

        df vazio acontece de proposito quando o cache atende 100% dos clientes (nenhuma
        chamada de API foi feita) - nao e caso de erro, e o cenario ideal. Retorna zeros
        em vez de {} para nao quebrar quem consome o resumo esperando as mesmas chaves."""
        df = self.para_dataframe()
        if df.empty:
            return {
                "chamadas_api": 0, "clientes": 0, "chamadas_por_cliente": 0.0,
                "tokens_entrada": 0, "tokens_saida": 0, "tokens_total": 0,
                "custo_total_usd": 0.0, "custo_medio_por_cliente_usd": 0.0,
                "latencia_total_s": 0.0, "latencia_media_por_chamada_s": 0.0,
                "latencia_p95_s": 0.0, "chamada_mais_cara_usd": 0.0,
                "por_tipo_turno": {}, "por_cliente": {},
                "projecao_30_clientes_usd": 0.0, "projecao_10k_clientes_usd": 0.0,
            }

        por_tipo = df.groupby("tipo_turno").agg(
            chamadas=("turno", "count"),
            tokens_total=("tokens_total", "sum"),
            custo_usd=("custo_usd", "sum"),
            latencia_media_s=("latencia_s", "mean"),
        )
        por_cliente = df.groupby("cliente_id").agg(
            chamadas=("turno", "count"),
            tokens_total=("tokens_total", "sum"),
            custo_usd=("custo_usd", "sum"),
            latencia_total_s=("latencia_s", "sum"),
        )

        custo_total = float(df["custo_usd"].sum())
        n_clientes = int(df["cliente_id"].nunique())

        return {
            "chamadas_api": int(len(df)),
            "clientes": n_clientes,
            "chamadas_por_cliente": round(len(df) / n_clientes, 2),
            "tokens_entrada": int(df["tokens_entrada"].sum()),
            "tokens_saida": int(df["tokens_saida"].sum()),
            "tokens_total": int(df["tokens_total"].sum()),
            "custo_total_usd": round(custo_total, 6),
            "custo_medio_por_cliente_usd": round(custo_total / n_clientes, 6),
            "latencia_total_s": round(float(df["latencia_s"].sum()), 2),
            "latencia_media_por_chamada_s": round(float(df["latencia_s"].mean()), 2),
            "latencia_p95_s": round(float(df["latencia_s"].quantile(0.95)), 2),
            "chamada_mais_cara_usd": round(float(df["custo_usd"].max()), 6),
            "por_tipo_turno": por_tipo.round(6).to_dict(orient="index"),
            "por_cliente": por_cliente.round(6).to_dict(orient="index"),
            # Extrapolacao: e a pergunta real por tras de medir custo.
            "projecao_30_clientes_usd": round(custo_total / n_clientes * 30, 6),
            "projecao_10k_clientes_usd": round(custo_total / n_clientes * 10_000, 2),
        }
