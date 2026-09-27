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


# ============================================================================
# Passo 4.0 - migracao v5 -> v6
# ============================================================================

MARCO_FASE_4 = "-- FASE 4 - O HUMANO DECIDE"


def _ddl_v5() -> str:
    """O DDL da v5 e o atual sem a secao da Fase 4 - que e exatamente a
    afirmacao de que o salto e aditivo. O teste abaixo prende essa afirmacao."""
    ddl = db.ESQUEMA_SQL.read_text(encoding="utf-8")
    return ddl[: ddl.index(MARCO_FASE_4)].rsplit("-- ====", 1)[0]


def test_secao_da_fase_4_so_adiciona(tmp_path):
    """Se alguem alterar uma tabela antiga na v6, o 5 em MIGRAVEIS_POR_ADICAO
    vira mentira. A secao nova so pode CRIAR coisa nova."""
    ddl = db.ESQUEMA_SQL.read_text(encoding="utf-8")
    secao = ddl[ddl.index(MARCO_FASE_4):]
    sem_comentario = "\n".join(l for l in secao.splitlines() if not l.strip().startswith("--"))
    comandos = [c.strip() for c in sem_comentario.split(";") if c.strip()]
    assert comandos, "secao da Fase 4 vazia?"
    for c in comandos:
        # corpo de trigger (SELECT RAISE...) e o END que o fecha sao parte do CREATE TRIGGER
        if c.upper().startswith(("SELECT RAISE", "END")):
            continue
        assert c.upper().startswith(("CREATE TABLE IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS",
                                     "CREATE TRIGGER IF NOT EXISTS")), c


def _banco_v5(caminho):
    conn = sqlite3.connect(caminho)
    conn.executescript(_ddl_v5())
    conn.execute("PRAGMA user_version = 5")
    conn.execute(
        "INSERT INTO lotes_ingestao (id, origem, sha256_arquivo, taxa_cambio_usd_brl, "
        "operacoes_brutas, operacoes_inseridas, duplicatas_ignoradas, "
        "datas_nulas_brutas, operacoes_usd_brutas, ingerido_em) "
        "VALUES (1, 'x.json', 'abc', 5.4, 1, 1, 0, 0, 0, '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()


def test_banco_v5_migra_para_v6_sem_perder_dado(tmp_path):
    caminho = tmp_path / "mesa.db"
    _banco_v5(caminho)

    conn = db.conectar(caminho)
    tabelas = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"transicoes", "decisoes"} <= tabelas
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM lotes_ingestao").fetchone()[0] == 1
    conn.close()


def test_banco_de_versao_antiga_nao_aditiva_e_recusado(tmp_path):
    """A brecha que existia: aplicar_esquema() num banco v4 so carimbava a versao
    nova - e a coluna aderencia.marcas_json (v5) nao aparecia, porque CREATE TABLE
    IF NOT EXISTS nao altera tabela existente. O codigo passava a ler uma coluna
    que nao estava la."""
    caminho = tmp_path / "mesa.db"
    _banco_v5(caminho)
    bruta = sqlite3.connect(caminho)
    bruta.execute("PRAGMA user_version = 4")
    bruta.commit()
    bruta.close()

    with pytest.raises(RuntimeError, match="nao e so aditivo"):
        db.conectar(caminho)
    bruta = sqlite3.connect(caminho)
    assert bruta.execute("PRAGMA user_version").fetchone()[0] == 4  # nao carimbou
    bruta.close()


def _alerta_minimo(conn):
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
    conn.execute(
        "INSERT INTO alertas (id, execucao_id, cliente_id, origem, "
        "sinalizacoes_fracionamento, sinalizacoes_valor_atipico, total_sinalizacoes, "
        "volume_total_brl, qtd_operacoes, nivel_risco_regra, estado, criado_em) "
        "VALUES (1, 1, 'CLI-1', 'regra', 0, 1, 1, 100.0, 3, 'médio', 'concluido', "
        "'2026-01-01T00:00:00Z')"
    )


def test_transicoes_e_decisoes_sao_append_only(tmp_path):
    conn = db.conectar(tmp_path / "mesa.db")
    _alerta_minimo(conn)
    conn.execute(
        "INSERT INTO transicoes (alerta_id, estado_anterior, estado_novo, ator, ator_tipo, "
        "registrado_em) VALUES (1, 'em_analise', 'concluido', 'ana', 'analista', 'x')"
    )
    conn.execute(
        "INSERT INTO decisoes (alerta_id, analista_id, decisao, nivel_risco_analista, "
        "motivo, decidido_em) VALUES (1, 'ana', 'discordo', 'alto', 'porque sim', 'x')"
    )
    conn.commit()

    for sql in ("UPDATE transicoes SET ator = 'bruno'", "DELETE FROM transicoes",
                "UPDATE decisoes SET decisao = 'concordo'", "DELETE FROM decisoes"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
    conn.close()


@pytest.mark.parametrize("decisao,analista,agente,motivo", [
    ("concordo", "alto", None, None),        # concordar com o que? nao ha nivel do agente
    ("concordo", "alto", "médio", None),     # "concordo" com nivel diferente do agente
    ("discordo", None, "médio", "x"),        # discordar sem dizer qual nivel
    ("discordo", "alto", "médio", "   "),    # discordar sem motivo
    ("escalar", None, "médio", None),        # escalar sem motivo
    ("discordo", "medio", "alto", "x"),      # nivel fora do vocabulario (sem acento)
    # os tres abaixo pegaram um bug real: CHECK com expressao NULL passa
    ("concordo", None, "médio", None),       # concordo sem nivel do analista
    ("discordo", "alto", "médio", None),     # discordar com motivo NULL
])
def test_regras_de_cada_decisao_valem_no_schema(tmp_path, decisao, analista, agente, motivo):
    """A API valida antes, mas a regra tem que valer para qualquer INSERT."""
    conn = db.conectar(tmp_path / "mesa.db")
    _alerta_minimo(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO decisoes (alerta_id, analista_id, decisao, nivel_risco_analista, "
            "nivel_risco_agente, motivo, decidido_em) VALUES (1, 'ana', ?, ?, ?, ?, 'x')",
            (decisao, analista, agente, motivo),
        )
    conn.close()


def test_uma_decisao_por_caso(tmp_path):
    conn = db.conectar(tmp_path / "mesa.db")
    _alerta_minimo(conn)
    sql = ("INSERT INTO decisoes (alerta_id, analista_id, decisao, motivo, decidido_em) "
           "VALUES (1, ?, 'escalar', 'grave', 'x')")
    conn.execute(sql, ("ana",))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(sql, ("bruno",))
    conn.close()
