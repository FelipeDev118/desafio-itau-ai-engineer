"""Testes de nivel_2/confronto.py — em especial o critério de nível esperado, que
ganhou o ramo "baixo" quando o lote passou a cobrir os 30 clientes (não só os
sinalizados) — ver DECISOES.md, "Cobrir os 30 clientes"."""
from confronto import _normalizar_nivel, nivel_risco_esperado


def test_fracionamento_espera_alto_mesmo_sem_atipicas():
    assert nivel_risco_esperado(flag_fracionamento=True, qtd_atipicas=0) == "alto"


def test_duas_ou_mais_atipicas_espera_alto():
    assert nivel_risco_esperado(flag_fracionamento=False, qtd_atipicas=2) == "alto"


def test_exatamente_uma_atipica_espera_medio():
    assert nivel_risco_esperado(flag_fracionamento=False, qtd_atipicas=1) == "médio"


def test_nenhuma_flag_espera_baixo():
    assert nivel_risco_esperado(flag_fracionamento=False, qtd_atipicas=0) == "baixo"


def test_normalizar_nivel_aceita_medio_sem_acento():
    assert _normalizar_nivel("medio") == "médio"
    assert _normalizar_nivel("MÉDIO") == "médio"
    assert _normalizar_nivel("alto") == "alto"
    assert _normalizar_nivel(None) is None
