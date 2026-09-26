"""Fase 2 - contrato da API, um bloco por passo do ROADMAP.

Os stores de teste sao montados UMA vez por sessao (ingestao + regras + cache
importado + triagem) e copiados por arquivo para cada teste que escreve. Montar
do zero em cada teste custaria ~2s x dezenas de testes sem testar nada a mais.

O bloco de concorrencia no fim sobe um uvicorn de verdade. Nao e excesso: o
TestClient sequencial PASSOU com o bug das threads do sqlite presente, e o
servidor real sob carga deu 500 em 278 de 300 requisicoes. Teste que nao
exercita a condicao do defeito nao protege contra ele.
"""
import concurrent.futures
import shutil
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

import agente
from dados import PARAMETROS_REGRAS, aplicar_regras, ranking_clientes_sinalizados
from mesa import api, db, regras_run, repositorio, triagem
from mesa.importar_cache import importar
from mesa.ingestao import ingerir

DADOS_REAIS = db.RAIZ / "dados" / "dados_nivel_2.json"


# ============================================================================
# Stores de teste
# ============================================================================


def _montar(caminho, triar: bool):
    conn = db.conectar(caminho)
    ingerir(conn, DADOS_REAIS)
    regras_run.executar(conn)
    if triar:
        importar(conn)

        def nao_deve_chamar(**kwargs):
            raise AssertionError("chamou a API do LLM: o cache importado cobre os 30")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(agente.CLIENT.chat.completions, "create", nao_deve_chamar)
            triagem.triar(conn, pausa_s=0, verbose=False)
    # checkpoint antes de fechar: garante que o .db copiado depois esta completo,
    # sem depender do arquivo -wal
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


@pytest.fixture(scope="session")
def _template_triado(tmp_path_factory):
    caminho = tmp_path_factory.mktemp("tpl") / "triado.db"
    _montar(caminho, triar=True)
    return caminho


@pytest.fixture(scope="session")
def _template_novo(tmp_path_factory):
    caminho = tmp_path_factory.mktemp("tpl") / "novo.db"
    _montar(caminho, triar=False)
    return caminho


def _cliente(template, tmp_path, monkeypatch) -> TestClient:
    caminho = tmp_path / "mesa.db"
    shutil.copy(template, caminho)
    monkeypatch.setattr(db, "CAMINHO_PADRAO", caminho)
    return TestClient(api.app)


@pytest.fixture
def cliente(_template_triado, tmp_path, monkeypatch):
    """Os 30 alertas triados - o estado depois de uma rodada completa do worker."""
    return _cliente(_template_triado, tmp_path, monkeypatch)


@pytest.fixture
def cliente_novo(_template_novo, tmp_path, monkeypatch):
    """Regras rodadas, nenhum alerta triado ainda - nenhum parecer no store."""
    return _cliente(_template_novo, tmp_path, monkeypatch)


def _alerta_de(cliente, cliente_id: str) -> int:
    itens = cliente.get("/fila?origem=todos&limite=200").json()["itens"]
    return next(i["alerta_id"] for i in itens if i["cliente_id"] == cliente_id)


# ============================================================================
# 2.0 - app, conexao, /saude
# ============================================================================


def test_saude_prova_que_a_api_le_o_store(cliente):
    r = cliente.get("/saude")
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["versao_esquema"] == db.VERSAO_ESQUEMA
    assert corpo["contagens"]["operacoes"] == 317
    assert corpo["contagens"]["alertas"] == 30
    assert corpo["execucao_atual"] == 1


def test_store_inexistente_devolve_503_e_nao_cria_banco_vazio(tmp_path, monkeypatch):
    """conectar() criaria um banco vazio em silencio, e a tela mostraria "0 casos"
    como se fosse verdade. A API recusa em vez de inventar."""
    caminho = tmp_path / "nao-existe.db"
    monkeypatch.setattr(db, "CAMINHO_PADRAO", caminho)
    r = TestClient(api.app).get("/saude")
    assert r.status_code == 503
    assert "mesa.ingestao" in r.json()["detail"]
    assert not caminho.exists()


def test_esquema_de_outra_versao_devolve_503(cliente, monkeypatch):
    monkeypatch.setattr(db, "VERSAO_ESQUEMA", db.VERSAO_ESQUEMA + 1)
    r = cliente.get("/saude")
    assert r.status_code == 503
    assert "versao de esquema" in r.json()["detail"]


def test_leitura_nao_escreve_no_store(cliente):
    """Uma API de leitura que aplica esquema ou atualiza PRAGMA a cada GET e uma
    API que escreve. O arquivo tem que sair intocado."""
    caminho = db.CAMINHO_PADRAO
    antes = caminho.read_bytes()
    for rota in ["/saude", "/fila", "/fila?origem=todos", "/execucoes"]:
        assert cliente.get(rota).status_code == 200
    assert caminho.read_bytes() == antes


def test_cors_aceita_localhost_e_recusa_origem_externa(cliente):
    local = cliente.get("/saude", headers={"Origin": "http://localhost:5500"})
    assert local.headers.get("access-control-allow-origin") == "http://localhost:5500"

    externa = cliente.get("/saude", headers={"Origin": "https://exemplo.com"})
    assert "access-control-allow-origin" not in externa.headers


# ============================================================================
# 2.1 - GET /fila
# ============================================================================


def test_fila_tem_a_mesma_ordem_do_ranking_das_regras(cliente):
    """<<< aceite do 2.1 >>>"""
    fila = [i["cliente_id"] for i in cliente.get("/fila").json()["itens"]]

    conn = db.conectar(db.CAMINHO_PADRAO)
    df = aplicar_regras(repositorio.operacoes_df(conn))
    conn.close()
    ranking = ranking_clientes_sinalizados(df, top_n=100)["cliente_id"].tolist()

    assert fila == ranking


def test_fila_separa_regra_de_controle(cliente):
    """<<< aceite do 2.1 >>> 17 por padrao, 30 com todos, 13 de controle."""
    assert cliente.get("/fila").json()["total"] == 17
    assert cliente.get("/fila?origem=todos").json()["total"] == 30
    controle = cliente.get("/fila?origem=controle").json()
    assert controle["total"] == 13
    assert all(i["total_sinalizacoes"] == 0 for i in controle["itens"])


def test_fila_reproduz_a_concordancia_do_confronto(cliente):
    """O `concorda` da fila tem que dar os mesmos 23/30 do confronto_resumo.json -
    a API reusa a normalizacao do confronto.py em vez de reescrever."""
    itens = cliente.get("/fila?origem=todos").json()["itens"]
    assert sum(i["concorda"] for i in itens) == 23
    assert sum(i["fundamentado"] for i in itens) == 28


def test_fila_normaliza_o_nivel_do_agente(cliente):
    """O modelo alterna 'medio' e 'médio'. Duas grafias na API empurrariam a
    normalizacao para a tela - uma segunda copia da regra."""
    niveis = {i["nivel_risco_agente"] for i in cliente.get("/fila?origem=todos").json()["itens"]}
    assert niveis <= {"baixo", "médio", "alto"}


def test_sem_parecer_concorda_e_null_nao_false(cliente_novo):
    """"Ainda nao triado" nao e "discordou". A fila tem que distinguir."""
    itens = cliente_novo.get("/fila").json()["itens"]
    assert itens, "fila vazia - o teste nao exercita nada"
    for item in itens:
        assert item["nivel_risco_agente"] is None
        assert item["concorda"] is None
        assert item["fundamentado"] is None


def test_fila_filtra_por_estado(cliente_novo):
    assert cliente_novo.get("/fila?estado=novo").json()["total"] == 17
    assert cliente_novo.get("/fila?estado=triado").json()["total"] == 0


def test_estado_invalido_na_query_e_422(cliente):
    assert cliente.get("/fila?estado=inventado").status_code == 422


def test_paginacao_percorre_a_fila_inteira_sem_repetir(cliente):
    completa = [i["cliente_id"] for i in cliente.get("/fila?origem=todos").json()["itens"]]

    vistos, cursor = [], None
    while True:
        url = "/fila?origem=todos&limite=7" + (f"&cursor={cursor}" if cursor else "")
        pagina = cliente.get(url).json()
        vistos += [i["cliente_id"] for i in pagina["itens"]]
        cursor = pagina["proximo_cursor"]
        if cursor is None:
            break

    assert vistos == completa
    assert len(set(vistos)) == 30


def test_paginacao_nao_pula_caso_quando_a_fila_muda_entre_paginas(cliente):
    """O motivo de o cursor ser por CHAVE e nao por offset. Um analista pega um
    caso da 1a pagina enquanto outro le a 2a: com offset, o caso some do filtro,
    tudo desloca uma posicao e um item e PULADO sem ninguem perceber."""
    p1 = cliente.get("/fila?estado=triado&limite=5").json()
    esperado_restante = [
        i["cliente_id"] for i in cliente.get("/fila?estado=triado").json()["itens"]
    ][5:]

    pego = p1["itens"][0]["alerta_id"]
    r = cliente.post(f"/alertas/{pego}/estado", json={"estado": "em_analise"},
                     headers={"X-Analista": "ana"})
    assert r.status_code == 200

    p2 = cliente.get(f"/fila?estado=triado&limite=50&cursor={p1['proximo_cursor']}").json()
    assert [i["cliente_id"] for i in p2["itens"]] == esperado_restante


def test_cursor_invalido_e_400(cliente):
    r = cliente.get("/fila?cursor=isto-nao-e-um-cursor")
    assert r.status_code == 400
    assert "cursor" in r.json()["detail"]


def test_limite_fora_da_faixa_e_422(cliente):
    assert cliente.get("/fila?limite=0").status_code == 422
    assert cliente.get("/fila?limite=10000").status_code == 422


def test_execucao_inexistente_na_fila_e_404(cliente):
    assert cliente.get("/fila?execucao_id=999").status_code == 404


# ============================================================================
# 2.2 - GET /alertas/{id}
# ============================================================================


def test_alerta_inexistente_e_404(cliente):
    """<<< aceite do 2.2 >>>"""
    r = cliente.get("/alertas/99999")
    assert r.status_code == 404
    assert r.json() == {"detail": "alerta 99999 nao existe"}


def test_caso_com_fracionamento_traz_a_data_que_disparou(cliente):
    """<<< aceite do 2.2 >>>"""
    conn = db.conectar(db.CAMINHO_PADRAO)
    cliente_frac = conn.execute(
        "SELECT cliente_id FROM sinalizacoes WHERE regra='fracionamento' LIMIT 1"
    ).fetchone()[0]
    esperadas = regras_run.datas_fracionamento_do_store(conn, cliente_frac)
    conn.close()

    caso = cliente.get(f"/alertas/{_alerta_de(cliente, cliente_frac)}").json()
    frac = [s for s in caso["sinalizacoes"] if s["regra"] == "fracionamento"]
    assert [s["data"] for s in frac] == esperadas
    assert frac[0]["operacao_id"] is None
    assert frac[0]["detalhe"]["soma_do_dia"] > PARAMETROS_REGRAS["frac_soma_min"]


def test_flags_das_operacoes_vem_das_sinalizacoes_gravadas(cliente):
    """A API marca a operacao atipica pelo que as regras GRAVARAM, nao por
    recalculo. As marcas tem que coincidir uma a uma com as sinalizacoes."""
    caso = cliente.get(f"/alertas/{_alerta_de(cliente, 'CLI-028')}").json()
    marcadas = {o["id"] for o in caso["operacoes"] if o["flag_valor_atipico"]}
    sinalizadas = {s["operacao_id"] for s in caso["sinalizacoes"] if s["regra"] == "valor_atipico"}
    assert marcadas == sinalizadas
    assert len(marcadas) == 2  # as duas de R$ 27.715,48 e R$ 24.875,39


def test_caso_cli_028_mostra_o_erro_que_a_aderencia_pegou(cliente):
    """O caso da apresentacao: o parecer chama de atipica uma operacao que nao e.
    A API tem que entregar a tela tudo que ela precisa para mostrar isso."""
    caso = cliente.get(f"/alertas/{_alerta_de(cliente, 'CLI-028')}").json()
    assert caso["aderencia"]["fundamentado"] is False
    assert [d["valor"] for d in caso["aderencia"]["atipicos_incorretos"]] == [6913.84]
    citada = next(o for o in caso["operacoes"] if abs(o["valor_brl"] - 6913.84) < 0.01)
    assert citada["flag_valor_atipico"] is False


def test_historico_expoe_as_versoes_anteriores_do_parecer(cliente):
    """O append-only visivel: CLI-014 tem pareceres importados de versoes
    antigas do prompt, e o analista tem que poder ve-los."""
    caso = cliente.get(f"/alertas/{_alerta_de(cliente, 'CLI-014')}").json()
    historico = caso["historico_parecer"]
    assert len(historico) > 1
    assert {h["origem_registro"] for h in historico} == {"importado_cache"}
    assert historico[0]["parecer_id"] == caso["parecer"]["parecer_id"]  # o mais recente e o atual


def test_caso_sem_parecer_devolve_parecer_e_aderencia_null(cliente_novo):
    caso = cliente_novo.get(f"/alertas/{_alerta_de(cliente_novo, 'CLI-014')}").json()
    assert caso["parecer"] is None
    assert caso["aderencia"] is None
    assert caso["historico_parecer"] == []
    assert len(caso["operacoes"]) == 11


def test_operacao_sem_data_vem_no_fim(cliente):
    conn = db.conectar(db.CAMINHO_PADRAO)
    com_nula = conn.execute(
        "SELECT cliente_id FROM operacoes WHERE data IS NULL LIMIT 1"
    ).fetchone()[0]
    conn.close()

    datas = [o["data"] for o in
             cliente.get(f"/alertas/{_alerta_de(cliente, com_nula)}").json()["operacoes"]]
    primeira_nula = datas.index(None)
    assert all(d is None for d in datas[primeira_nula:])


# ============================================================================
# 2.3 - GET /alertas/{id}/evidencias
# ============================================================================


def test_alerta_reaproveitado_tambem_devolve_evidencia(cliente):
    """<<< aceite do 2.3 >>> A copia do passo 1.5 carrega as evidencias junto."""
    caso = cliente.get(f"/alertas/{_alerta_de(cliente, 'CLI-014')}").json()
    assert caso["parecer"]["reaproveitado_de"] is not None

    ev = cliente.get(f"/alertas/{caso['alerta']['alerta_id']}/evidencias").json()
    assert len(ev) >= 1
    assert ev[0]["tool"] == "historico_cliente"
    assert ev[0]["args"] == {"cliente_id": "CLI-014"}


def test_evidencia_importada_nao_tem_payload(cliente):
    """Fato do historico, nao bug: o cache antigo nunca guardou o retorno das
    ferramentas (so a partir do passo 1.4). A API devolve null em vez de
    inventar um payload."""
    ev = cliente.get(f"/alertas/{_alerta_de(cliente, 'CLI-014')}/evidencias").json()
    assert all(e["payload"] is None for e in ev)


def test_alerta_sem_parecer_devolve_lista_vazia(cliente_novo):
    """<<< aceite do 2.3 >>> O alerta existe, so nao foi triado: [] e nao 404."""
    r = cliente_novo.get(f"/alertas/{_alerta_de(cliente_novo, 'CLI-014')}/evidencias")
    assert r.status_code == 200
    assert r.json() == []


def test_evidencias_de_alerta_inexistente_e_404(cliente):
    assert cliente.get("/alertas/99999/evidencias").status_code == 404


# ============================================================================
# 2.4 - POST /alertas/{id}/estado   e   2.5 - X-Analista
# ============================================================================


def _mudar(cliente, alerta_id, estado, analista="ana"):
    headers = {"X-Analista": analista} if analista is not None else {}
    return cliente.post(f"/alertas/{alerta_id}/estado", json={"estado": estado}, headers=headers)


def _estado(cliente, alerta_id):
    return cliente.get(f"/alertas/{alerta_id}").json()["alerta"]


def test_novo_para_concluido_e_409_e_nao_altera_o_estado(cliente_novo):
    """<<< aceite do 2.4 >>>"""
    aid = _alerta_de(cliente_novo, "CLI-014")
    r = _mudar(cliente_novo, aid, "concluido")
    assert r.status_code == 409
    assert _estado(cliente_novo, aid)["estado"] == "novo"


def test_concluir_exige_a_fase_4_mesmo_vindo_de_em_analise(cliente):
    """Ambiguidade do ROADMAP resolvida: em_analise -> concluido estava listada
    como permitida, mas 'so via Fase 4, com decisao junto'. Liberar agora
    permitiria fechar um caso sem registro do que o analista decidiu."""
    aid = _alerta_de(cliente, "CLI-014")
    assert _mudar(cliente, aid, "em_analise").status_code == 200

    r = _mudar(cliente, aid, "concluido")
    assert r.status_code == 409
    assert "Fase 4" in r.json()["detail"]
    assert _estado(cliente, aid)["estado"] == "em_analise"


def test_pegar_caso_triado_grava_o_analista(cliente):
    aid = _alerta_de(cliente, "CLI-014")
    r = _mudar(cliente, aid, "em_analise", analista="ana.souza")
    assert r.status_code == 200
    assert r.json() == {
        "alerta_id": aid, "estado_anterior": "triado",
        "estado": "em_analise", "analista_id": "ana.souza",
    }
    assert _estado(cliente, aid)["analista_id"] == "ana.souza"


def test_devolver_para_a_fila_libera_o_dono(cliente):
    aid = _alerta_de(cliente, "CLI-014")
    _mudar(cliente, aid, "em_analise")
    r = _mudar(cliente, aid, "triado")
    assert r.status_code == 200
    depois = _estado(cliente, aid)
    assert depois["estado"] == "triado"
    assert depois["analista_id"] is None


def test_caso_ja_em_analise_nao_pode_ser_pego_de_novo(cliente):
    """Sem esta recusa, um segundo analista "roubaria" o caso em silencio."""
    aid = _alerta_de(cliente, "CLI-014")
    assert _mudar(cliente, aid, "em_analise", analista="ana").status_code == 200

    r = _mudar(cliente, aid, "em_analise", analista="bruno")
    assert r.status_code == 409
    assert _estado(cliente, aid)["analista_id"] == "ana"


def test_api_nao_duplica_o_caminho_do_worker(cliente_novo):
    """novo -> triado e escrito pelo worker, que rodou o agente. A API nao pode
    marcar como triado um caso que nenhum agente viu."""
    aid = _alerta_de(cliente_novo, "CLI-014")
    assert _mudar(cliente_novo, aid, "triado").status_code == 409


def test_mensagem_de_409_diz_o_que_era_permitido(cliente_novo):
    r = _mudar(cliente_novo, _alerta_de(cliente_novo, "CLI-014"), "triado")
    assert "em_analise" in r.json()["detail"]


def test_mudar_estado_de_alerta_inexistente_e_404(cliente):
    assert _mudar(cliente, 99999, "em_analise").status_code == 404


def test_estado_de_destino_invalido_e_422(cliente):
    assert _mudar(cliente, _alerta_de(cliente, "CLI-014"), "arquivado").status_code == 422


def test_sem_x_analista_e_400(cliente):
    """<<< aceite do 2.5 >>>"""
    aid = _alerta_de(cliente, "CLI-014")
    r = _mudar(cliente, aid, "em_analise", analista=None)
    assert r.status_code == 400
    assert "X-Analista" in r.json()["detail"]
    assert _estado(cliente, aid)["estado"] == "triado"


def test_x_analista_em_branco_tambem_e_400(cliente):
    assert _mudar(cliente, _alerta_de(cliente, "CLI-014"), "em_analise", analista="   ").status_code == 400


def test_get_nao_exige_x_analista(cliente):
    """<<< aceite do 2.5 >>> Obrigatorio so onde ha escrita."""
    assert cliente.get("/fila").status_code == 200


# ============================================================================
# 2.6 - GET /execucoes
# ============================================================================


def test_execucao_devolve_os_parametros_gravados_nao_os_do_codigo(cliente):
    """<<< aceite do 2.6 >>> Roda uma segunda execucao com outro limiar: a API
    tem que devolver o que cada uma usou, nao o PARAMETROS_REGRAS de hoje."""
    conn = db.conectar(db.CAMINHO_PADRAO)
    regras_run.executar(conn, {**PARAMETROS_REGRAS, "atipico_fator": 10})
    conn.close()

    execucoes = cliente.get("/execucoes").json()
    assert [e["parametros"]["atipico_fator"] for e in execucoes] == [10, 5]  # mais recente primeiro

    primeira = cliente.get("/execucoes/1").json()
    assert primeira["parametros"] == PARAMETROS_REGRAS
    assert (primeira["alertas_regra"], primeira["alertas_controle"]) == (17, 13)


def test_execucao_inexistente_e_404(cliente):
    assert cliente.get("/execucoes/999").status_code == 404


# ============================================================================
# Concorrencia - com servidor de verdade
# ============================================================================


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(_template_triado, tmp_path, monkeypatch):
    caminho = tmp_path / "mesa.db"
    shutil.copy(_template_triado, caminho)
    monkeypatch.setattr(db, "CAMINHO_PADRAO", caminho)

    porta = _porta_livre()
    srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=porta, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    limite = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < limite, "uvicorn nao subiu em 10s"
        time.sleep(0.05)

    yield f"http://127.0.0.1:{porta}"

    srv.should_exit = True
    thread.join(timeout=5)


def test_leituras_concorrentes_nao_quebram(servidor):
    """Regressao do bug medido: com check_same_thread no padrao, 278 de 300
    requisicoes concorrentes deram 500. O TestClient sequencial nao pega isso."""
    def get(_):
        return httpx.get(f"{servidor}/fila?origem=todos", timeout=10).status_code

    with concurrent.futures.ThreadPoolExecutor(32) as ex:
        status = list(ex.map(get, range(300)))

    assert status.count(200) == 300, {s: status.count(s) for s in set(status)}


def test_dois_analistas_nao_pegam_o_mesmo_caso(servidor):
    """20 analistas clicam em "pegar caso" ao mesmo tempo. Exatamente um vence;
    os outros recebem 409.

    Este teste e PROBABILISTICO: depende de as threads se intercalarem no
    momento certo. Reintroduzindo o UPDATE sem condicao, ele falhou em 2 de 3
    execucoes - e passou na terceira com o bug presente. Fica porque exercita o
    cenario real sob carga, mas quem PROTEGE contra a regressao e o teste
    deterministico logo abaixo."""
    alerta_id = next(
        i["alerta_id"] for i in httpx.get(f"{servidor}/fila").json()["itens"]
        if i["cliente_id"] == "CLI-014"
    )

    def pegar(n):
        return httpx.post(
            f"{servidor}/alertas/{alerta_id}/estado", json={"estado": "em_analise"},
            headers={"X-Analista": f"analista-{n}"}, timeout=10,
        ).status_code

    with concurrent.futures.ThreadPoolExecutor(20) as ex:
        status = list(ex.map(pegar, range(20)))

    assert status.count(200) == 1, status
    assert status.count(409) == 19, status
    dono = httpx.get(f"{servidor}/alertas/{alerta_id}").json()["alerta"]["analista_id"]
    assert dono.startswith("analista-")


def test_estado_que_muda_entre_a_leitura_e_a_escrita_e_409(cliente, monkeypatch):
    """Versao DETERMINISTICA da corrida acima - nao depende de sorte de thread.

    Forca a intercalacao exata: a requisicao le o estado ('triado'), OUTRO
    analista pega o caso nesse intervalo, e so entao o UPDATE roda. Com o
    compare-and-set, o UPDATE nao encontra mais 'triado' e devolve 409. Com um
    UPDATE sem condicao, o segundo analista sobrescreveria o primeiro em
    silencio - e este teste falharia sempre, nao 2 vezes em 3."""
    aid = _alerta_de(cliente, "CLI-014")
    ler_original = api._alerta_ou_404

    def ler_e_deixar_outro_analista_passar_na_frente(conn, alerta_id):
        linha = ler_original(conn, alerta_id)  # le 'triado'
        outra = db.conectar(db.CAMINHO_PADRAO)
        outra.execute("UPDATE alertas SET estado='em_analise', analista_id='bruno' "
                      "WHERE id = ?", (alerta_id,))
        outra.commit()
        outra.close()
        return linha  # devolve a leitura ja desatualizada

    monkeypatch.setattr(api, "_alerta_ou_404", ler_e_deixar_outro_analista_passar_na_frente)
    r = _mudar(cliente, aid, "em_analise", analista="ana")
    monkeypatch.setattr(api, "_alerta_ou_404", ler_original)

    assert r.status_code == 409
    assert "mudou de estado" in r.json()["detail"]
    assert _estado(cliente, aid)["analista_id"] == "bruno"  # quem chegou primeiro fica
