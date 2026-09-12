"""Testes da verificação de aderência (nivel_2/verificacao_aderencia.py) — em especial
o caso de regressão descoberto ao reexecutar o lote depois do fix de max_turnos: um
cliente com número ÍMPAR de operações tem, por definição matemática, a mediana igual
ao valor de uma operação real do meio da distribuição. Se essa operação for consultada
antes do agregado, o checker atribuía a ela citações que na verdade eram da mediana
("valor mediano de R$X, indicando transações atípicas") — falso positivo de "atípico
incorreto" em CLI-014 e CLI-005 na base real.
"""
import pandas as pd
import pytest

from verificacao_aderencia import _referencias_validas, _parsear_valor_brl, extrair_valores, verificar


def _df_cliente(valores, cliente_id="CLI-1", flags_atipico=None, canais=None, fracionamento=False):
    flags_atipico = flags_atipico or [False] * len(valores)
    canais = canais or ["pix"] * len(valores)
    return pd.DataFrame(
        [
            {
                "id": f"OP-{i}",
                "cliente_id": cliente_id,
                "valor_brl": v,
                "flag_valor_atipico": flags_atipico[i],
                "flag_fracionamento": fracionamento,
                "data": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
                "data_valida": True,
                "canal": canais[i],
            }
            for i, v in enumerate(valores)
        ]
    )


def test_mediana_tem_prioridade_sobre_operacao_coincidente():
    # 5 operacoes (impar): a mediana (300) e, por definicao, o valor da operacao do meio
    # valores assimetricos (media != mediana), como no caso real: mediana=100, media=472
    df = _df_cliente([50.0, 80.0, 100.0, 130.0, 2000.0])
    refs = _referencias_validas("CLI-1", df)
    fonte = next(nome for nome, v in refs.items() if abs(v - 100.0) <= 0.5)
    assert fonte == "mediana_cliente"


def test_mencao_generica_a_mediana_nao_e_atipico_incorreto():
    # frase no estilo real do LLM: cita a mediana como agregado, nao como a propria
    # operacao atipica - "atipico" aparece na frase, mas qualificando o padrao geral
    df = _df_cliente([50.0, 80.0, 100.0, 130.0, 2000.0])
    justificativa = (
        "O cliente tem valor mediano de R$100,00, indicando presenca de "
        "transacoes de valor atipico."
    )
    resultado = verificar("CLI-1", justificativa, df)
    assert resultado.atipicos_incorretos == []
    assert resultado.fundamentado is True


# ---------- extração/parsing de valores em R$ (regressão de formato) ----------
# Achado ao reauditar o lote reexecutado: o LLM escreve valores em formato americano
# ("R$71,297.68") e abreviado ("R$14.3k"), e o regex antigo truncava os dois -
# "71,297.68" virava 71.29, "14.3k" virava 14.0 - fazendo pareceres corretos
# parecerem não-fundamentados por um bug de parsing, não por erro do LLM.


def test_valor_formato_americano_com_milhar():
    assert _parsear_valor_brl("71,297.68") == pytest.approx(71297.68)


def test_valor_formato_br_com_milhar():
    assert _parsear_valor_brl("14.326,29") == pytest.approx(14326.29)


def test_valor_br_milhar_sem_decimais():
    assert _parsear_valor_brl("7.330") == pytest.approx(7330.0)


def test_valor_abreviado_com_k_multiplica_por_mil():
    achados = extrair_valores("entre R$14.3k e R$19.4k, totalizando R$71,297.68")
    assert [a["valor"] for a in achados] == [14300.0, 19400.0, 71297.68]


def test_faixa_entre_dois_valores_e_tratada_como_limiar_nos_dois_lados():
    # achado real em CLI-029/CLI-017: "entre R$14.3k e R$19.4k" e faixa aproximada dos
    # extremos reais (14326.29 e 19418.96) - nao uma citacao exata de nenhum dos dois
    achados = extrair_valores(
        "quatro operações (entre R$14.3k e R$19.4k), totalizando R$71,297.68"
    )
    assert [a["e_limiar"] for a in achados] == [True, True, False]


def test_soma_por_canal_e_referencia_valida():
    # achado real em CLI-030: "R$85.546,51 concentrado em duas operacoes TED" - soma
    # legitima de perfil_canal(), mas antes do fix nao existia como referencia
    df = _df_cliente(
        [77628.19, 7918.32, 4611.07],
        canais=["ted", "ted", "cartao"],
    )
    refs = _referencias_validas("CLI-1", df)
    assert refs["soma_canal_ted"] == pytest.approx(85546.51)


def test_agregado_abreviado_com_k_usa_tolerancia_maior():
    # achado real em CLI-007: "R$58.6k" e o volume real (58601.43) arredondado para 1
    # casa decimal de milhar - tolerancia de 0.5 rejeitaria uma citacao correta so
    # pela abreviacao. soma = 1000+2000+3000+52601.43 = 58601.43 -> "58.6k"
    df = _df_cliente([1000.0, 2000.0, 3000.0, 52601.43])
    justificativa = "O cliente movimentou um volume total de R$58.6k no periodo."
    resultado = verificar("CLI-1", justificativa, df)
    assert resultado.valores_nao_encontrados == []
    assert resultado.valores_confirmados == [{"valor": 58600.0, "fonte": "volume_total_cliente"}]


def test_cliente_sem_flags_e_sem_citacao_e_fundamentado():
    # achado real ao cobrir os 30 clientes: 6 clientes SEM nenhuma flag deterministica
    # tiveram parecer "baixo risco" sem nenhum R$ citado - correto (nao ha o que citar),
    # nao uma falha de fundamentacao
    df = _df_cliente([100.0, 200.0, 300.0], flags_atipico=[False, False, False])
    justificativa = "Sem indicadores de fracionamento ou valores atipicos."
    resultado = verificar("CLI-1", justificativa, df)
    assert resultado.fundamentado is True
    assert resultado.motivo == "cliente sem flags deterministicas - nada a fundamentar"


def test_cliente_com_flag_e_sem_citacao_continua_nao_fundamentado():
    df = _df_cliente([100.0, 200.0, 5000.0], flags_atipico=[False, False, True])
    justificativa = "Ha indicios de comportamento suspeito que merece atencao."
    resultado = verificar("CLI-1", justificativa, df)
    assert resultado.fundamentado is False


def test_citar_operacao_errada_como_atipica_ainda_e_detectado():
    # regressao: o fix acima nao pode mascarar uma alucinacao real. Aqui 140.0 nao
    # coincide com nenhum agregado (mediana=130, media~1098) - so pode ter vindo de
    # uma operacao, e a operacao real marcada como atipica e outra (5000.0)
    df = _df_cliente(
        [100.0, 120.0, 130.0, 140.0, 5000.0],
        flags_atipico=[False, False, False, False, True],
    )
    justificativa = "Foi identificada uma operacao atipica de R$140,00 nesse cliente."
    resultado = verificar("CLI-1", justificativa, df)
    assert resultado.fundamentado is False
    assert resultado.atipicos_incorretos == [{"valor": 140.0, "fonte": "operacao OP-3"}]
