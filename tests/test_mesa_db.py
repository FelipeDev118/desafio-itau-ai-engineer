"""Passo 0.1 - conexao e esquema.

O que estes testes protegem: FK ligada (o SQLite deixa DESLIGADA por padrao, e
por conexao - declarar REFERENCES no DDL sem ligar o PRAGMA e ter integridade
so no comentario) e idempotencia do esquema.
"""
import sqlite3

import pytest

from mesa import db


def test_conectar_cria_o_arquivo_e_as_tabelas(tmp_path):
    conn = db.conectar(tmp_path / "mesa.db")
    tabelas = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "lotes_ingestao",
        "operacoes",
        "execucoes_regras",
        "sinalizacoes",
        "alertas",
    } <= tabelas
    conn.close()


def test_foreign_keys_estao_ligadas(tmp_path):
    conn = db.conectar(tmp_path / "mesa.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_fk_invalida_e_rejeitada(tmp_path):
    """Nao basta o PRAGMA responder 1: a restricao tem que barrar de verdade."""
    conn = db.conectar(tmp_path / "mesa.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO operacoes (id, lote_id, cliente_id, data, data_valida, "
            "valor, moeda, valor_brl, canal, tipo, contraparte) "
            "VALUES ('OP-1', 999, 'CLI-1', '2026-01-01', 1, 10.0, 'BRL', 10.0, "
            "'pix', 'pagamento', 'Fulano')"
        )
        conn.commit()
    conn.close()


def test_aplicar_esquema_duas_vezes_nao_quebra(tmp_path):
    caminho = tmp_path / "mesa.db"
    conn = db.conectar(caminho)
    conn.execute(
        "INSERT INTO lotes_ingestao (origem, sha256_arquivo, taxa_cambio_usd_brl, "
        "operacoes_brutas, operacoes_inseridas, duplicatas_ignoradas, "
        "datas_nulas_brutas, operacoes_usd_brutas, ingerido_em) "
        "VALUES ('x.json', 'abc', 5.4, 1, 1, 0, 0, 0, '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    # reabrir aplica o esquema de novo; o dado tem que continuar la
    conn = db.conectar(caminho)
    assert conn.execute("SELECT COUNT(*) FROM lotes_ingestao").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.VERSAO_ESQUEMA
    conn.close()


def test_banco_de_versao_futura_e_recusado(tmp_path):
    """Escrever num esquema que este codigo nao entende e pior que falhar."""
    caminho = tmp_path / "mesa.db"
    conn = db.conectar(caminho)
    conn.execute(f"PRAGMA user_version = {db.VERSAO_ESQUEMA + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="mais antigo que o banco"):
        db.conectar(caminho)


def test_estado_de_alerta_invalido_e_rejeitado(tmp_path):
    """O CHECK do enum vive no schema, nao na confianca de quem faz o INSERT."""
    conn = db.conectar(tmp_path / "mesa.db")
    conn.execute(
        "INSERT INTO lotes_ingestao (id, origem, sha256_arquivo, taxa_cambio_usd_brl, "
        "operacoes_brutas, operacoes_inseridas, duplicatas_ignoradas, "
        "datas_nulas_brutas, operacoes_usd_brutas, ingerido_em) "
        "VALUES (1, 'x.json', 'abc', 5.4, 1, 1, 0, 0, 0, '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO execucoes_regras (id, lote_id, versao_regras, parametros_json, "
        "executado_em, operacoes_avaliadas, clientes_fracionamento, operacoes_atipicas) "
        "VALUES (1, 1, 'r1', '{}', '2026-01-01T00:00:00Z', 1, 0, 0)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO alertas (execucao_id, cliente_id, origem, "
            "sinalizacoes_fracionamento, sinalizacoes_valor_atipico, total_sinalizacoes, "
            "volume_total_brl, qtd_operacoes, nivel_risco_regra, estado, criado_em) "
            "VALUES (1, 'CLI-1', 'regra', 0, 1, 1, 100.0, 3, 'médio', 'inventado', "
            "'2026-01-01T00:00:00Z')"
        )
    conn.close()
