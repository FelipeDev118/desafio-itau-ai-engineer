"""Passos 0.2 e 0.3 - ingestao e idempotencia.

Os numeros conferidos aqui sao os da entrega (docs/REPRODUTIBILIDADE.md): se a
ingestao mudar qualquer um deles, ela esta errada - nao a entrega.
"""
import json

import pytest
from conftest import escrever_dataset, op

from mesa import db
from mesa.ingestao import ingerir

DADOS_REAIS = db.RAIZ / "dados" / "dados_nivel_2.json"


@pytest.fixture
def conn(tmp_path):
    c = db.conectar(tmp_path / "mesa.db")
    yield c
    c.close()


# ---------- 0.2: os numeros da entrega ----------


def test_ingestao_da_base_real_reproduz_os_numeros_da_entrega(conn):
    r = ingerir(conn, DADOS_REAIS)

    assert r.operacoes_brutas == 322
    assert r.operacoes_inseridas == 317
    assert r.duplicatas_ignoradas == 5
    assert conn.execute("SELECT COUNT(*) FROM operacoes").fetchone()[0] == 317
    assert conn.execute("SELECT COUNT(DISTINCT cliente_id) FROM operacoes").fetchone()[0] == 30


def test_estatisticas_do_arquivo_bruto_ficam_no_lote(conn):
    """As 7 datas nulas do ARQUIVO viram 6 na tabela porque uma delas esta numa
    das 5 duplicatas removidas. Os dois numeros estao certos, sobre populacoes
    diferentes - e ambos precisam ser recuperaveis do store."""
    ingerir(conn, DADOS_REAIS)

    lote = conn.execute("SELECT * FROM lotes_ingestao").fetchone()
    assert lote["datas_nulas_brutas"] == 7
    assert lote["operacoes_usd_brutas"] == 7
    assert lote["taxa_cambio_usd_brl"] == 5.4

    na_tabela = conn.execute(
        "SELECT COUNT(*) FROM operacoes WHERE data IS NULL"
    ).fetchone()[0]
    assert na_tabela == 6


def test_data_nula_grava_null_e_data_valida_zero(conn):
    ingerir(conn, DADOS_REAIS)
    incoerentes = conn.execute(
        "SELECT COUNT(*) FROM operacoes "
        "WHERE (data IS NULL) != (data_valida = 0)"
    ).fetchone()[0]
    assert incoerentes == 0


def test_conversao_usd_usa_a_taxa_do_lote(conn):
    ingerir(conn, DADOS_REAIS)
    fora = conn.execute(
        "SELECT COUNT(*) FROM operacoes "
        "WHERE moeda = 'USD' AND abs(valor_brl - valor * 5.4) > 1e-9"
    ).fetchone()[0]
    assert fora == 0

    intactas = conn.execute(
        "SELECT COUNT(*) FROM operacoes WHERE moeda = 'BRL' AND valor_brl != valor"
    ).fetchone()[0]
    assert intactas == 0


def test_taxa_e_do_lote_nao_uma_constante_global(conn, tmp_path):
    """Dois arquivos com taxas diferentes: cada operacao guarda a taxa que valia
    na SUA ingestao. E o que impede um alerta antigo de mudar de valor quando o
    cambio muda."""
    a = escrever_dataset(tmp_path, [op("OP-A", "CLI-1", "2026-01-01", 100.0, moeda="USD")], taxa=5.0)
    b = tmp_path / "b.json"
    b.write_text(
        json.dumps({"taxa_cambio_usd_brl": 6.0, "operacoes": [
            op("OP-B", "CLI-1", "2026-01-02", 100.0, moeda="USD")]}),
        encoding="utf-8",
    )

    ingerir(conn, a)
    ingerir(conn, b)

    valores = dict(conn.execute("SELECT id, valor_brl FROM operacoes").fetchall())
    assert valores["OP-A"] == pytest.approx(500.0)
    assert valores["OP-B"] == pytest.approx(600.0)


# ---------- 0.3: idempotencia ----------


def test_reingerir_o_mesmo_arquivo_nao_duplica(conn):
    primeira = ingerir(conn, DADOS_REAIS)
    segunda = ingerir(conn, DADOS_REAIS)

    assert primeira.operacoes_inseridas == 317
    assert segunda.operacoes_inseridas == 0
    assert segunda.duplicatas_ignoradas == 322
    assert conn.execute("SELECT COUNT(*) FROM operacoes").fetchone()[0] == 317
    # o lote da 2a tentativa fica registrado: "alguem tentou reingerir" e um fato
    assert conn.execute("SELECT COUNT(*) FROM lotes_ingestao").fetchone()[0] == 2


def test_operacao_continua_apontando_para_o_lote_que_a_trouxe(conn):
    primeira = ingerir(conn, DADOS_REAIS)
    ingerir(conn, DADOS_REAIS)
    do_segundo = conn.execute(
        "SELECT COUNT(*) FROM operacoes WHERE lote_id != ?", (primeira.lote_id,)
    ).fetchone()[0]
    assert do_segundo == 0


def test_arquivo_corrigido_nao_atualiza_operacao_existente(conn, tmp_path):
    """Divida conhecida (ROADMAP 5.2), fixada como teste para nao virar surpresa:
    reingerir o mesmo id com valor diferente NAO altera o que ja esta gravado."""
    a = escrever_dataset(tmp_path, [op("OP-1", "CLI-1", "2026-01-01", 100.0)])
    ingerir(conn, a)

    b = tmp_path / "corrigido.json"
    b.write_text(
        json.dumps({"taxa_cambio_usd_brl": 5.4, "operacoes": [
            op("OP-1", "CLI-1", "2026-01-01", 999.0)]}),
        encoding="utf-8",
    )
    ingerir(conn, b)

    assert conn.execute("SELECT valor FROM operacoes WHERE id='OP-1'").fetchone()[0] == 100.0
