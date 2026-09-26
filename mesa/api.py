"""Fase 2 - a API da Mesa de Triagem.

A API NAO calcula nada. Toda regra de negocio ja existe e ja rodou: as regras
gravaram sinalizacoes e alertas (Fase 0), o worker gravou pareceres, evidencias
e aderencia (Fase 1). Aqui so existe contrato HTTP sobre o que esta no store.

Isso e uma restricao deliberada, nao economia de esforco. Se a API recalculasse
"qual operacao e atipica" para desenhar a tela, passaria a existir uma segunda
resposta para uma pergunta que o store ja respondeu - e a que ninguem audita e a
que diverge. As duas unicas derivacoes aqui (`concorda` e o nivel normalizado)
reusam a normalizacao de nivel_2/confronto.py em vez de reescreve-la.

Rodar:
    uvicorn mesa.api:app --reload          # http://127.0.0.1:8000/docs
"""
import base64
import binascii
import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import mesa  # noqa: F401  - poe nivel_2/ no sys.path
from confronto import _normalizar_nivel
from mesa import db

Estado = Literal["novo", "triado", "em_analise", "concluido"]

# A tela da Fase 3 (HTML + JS puro, sem build) servida pela propria API: mesma
# origem, nenhum segundo servidor para subir, nenhum CORS para configurar.
WEB_DIR = Path(__file__).resolve().parent / "web"

# As transicoes que ESTE endpoint aceita. Tres, e so tres.
#
# Ficam de fora, de proposito:
#   novo -> triado          e do worker (mesa/triagem.py); a API nao duplica o
#                           caminho de quem roda o agente
#   em_analise -> concluido so com decisao registrada junto (Fase 4). Liberar
#                           agora permitiria concluir um caso sem registro do que
#                           o analista decidiu - o oposto de "o humano decide".
TRANSICOES = {
    ("novo", "em_analise"),     # analista pegou o caso antes da triagem
    ("triado", "em_analise"),   # analista pegou o caso triado
    ("em_analise", "triado"),   # analista devolveu o caso para a fila
}


# ============================================================================
# Contrato - os modelos SAO a especificacao que a tela (Fase 3) consome. O
# FastAPI valida a saida contra eles, entao um campo que some do SQL vira erro
# aqui, nao um `undefined` silencioso no navegador.
# ============================================================================


class Contagens(BaseModel):
    operacoes: int
    execucoes: int
    alertas: int
    pareceres: int


class Saude(BaseModel):
    status: Literal["ok"]
    versao_esquema: int
    execucao_atual: int | None
    contagens: Contagens


class ItemFila(BaseModel):
    alerta_id: int
    cliente_id: str
    estado: Estado
    origem: Literal["regra", "controle"]
    nivel_risco_regra: str
    # None = ainda nao ha parecer valido. NAO e o mesmo que "discorda": foi
    # exatamente essa confusao que o confronto.py teve que desfazer, separando
    # "respostas validas" de "concordancia" (docs/DECISOES.md).
    nivel_risco_agente: str | None
    concorda: bool | None
    fundamentado: bool | None
    total_sinalizacoes: int
    volume_total_brl: float
    qtd_operacoes: int
    analista_id: str | None
    criado_em: str


class Fila(BaseModel):
    execucao_id: int
    total: int
    itens: list[ItemFila]
    proximo_cursor: str | None


class Sinalizacao(BaseModel):
    regra: Literal["fracionamento", "valor_atipico"]
    operacao_id: str | None
    data: str | None
    detalhe: dict[str, Any]


class Parecer(BaseModel):
    parecer_id: int
    nivel_risco: str | None
    tipologia_suspeita: str | None
    red_flags: list[str]
    justificativa: str | None
    erro_parsing: str | None
    modelo: str
    versao_prompt: str
    origem_registro: str
    reaproveitado_de: int | None
    criado_em: str


class Marca(BaseModel):
    """Um valor em R$ citado na justificativa: onde esta (posicoes no texto) e
    como o verificador o classificou. A tela marca o trecho texto[inicio:fim]
    sem procurar numero nenhum por conta propria."""
    inicio: int
    fim: int
    valor: float
    classe: Literal["confirmado", "nao_encontrado", "atipico_incorreto", "limiar"]
    # "operacao OP-00269", "mediana_cliente", "soma_do_dia_2026-05-26",
    # "soma_canal_ted"... - o nome da referencia que conferiu, vindo do verificador
    fonte: str | None


class Aderencia(BaseModel):
    fundamentado: bool
    motivo: str
    valores_confirmados: list[Any]
    valores_nao_encontrados: list[Any]
    atipicos_incorretos: list[Any]
    marcas: list[Marca]


class Operacao(BaseModel):
    id: str
    data: str | None
    valor: float
    moeda: str
    valor_brl: float
    canal: str
    tipo: str
    contraparte: str
    # As duas marcas vem de `sinalizacoes`, gravadas quando as regras rodaram -
    # NAO de um recalculo aqui. E o que liga um numero do parecer a sua fonte.
    flag_valor_atipico: bool
    em_dia_de_fracionamento: bool


class VersaoParecer(BaseModel):
    parecer_id: int
    alerta_id: int | None
    nivel_risco: str | None
    origem_registro: str
    reaproveitado_de: int | None
    criado_em: str


class Caso(BaseModel):
    alerta: ItemFila
    execucao_id: int
    sinalizacoes: list[Sinalizacao]
    parecer: Parecer | None
    aderencia: Aderencia | None
    operacoes: list[Operacao]
    # O append-only ficando VISIVEL: se este cliente ja recebeu outro parecer,
    # o analista ve. E a razao de a Fase 1 existir.
    historico_parecer: list[VersaoParecer]
    # Para onde este caso pode ir a partir do estado atual, derivado da MESMA
    # tabela TRANSICOES que o POST aplica. A tela mostra so estes botoes em vez
    # de manter uma segunda copia da maquina de estados em JavaScript.
    transicoes_permitidas: list[Estado]


class Evidencia(BaseModel):
    ordem: int
    tool: str
    args: dict[str, Any]
    payload: Any


class NovoEstado(BaseModel):
    estado: Estado


class Transicao(BaseModel):
    alerta_id: int
    estado_anterior: Estado
    estado: Estado
    analista_id: str | None


class Execucao(BaseModel):
    id: int
    lote_id: int
    versao_regras: str
    parametros: dict[str, Any]
    executado_em: str
    operacoes_avaliadas: int
    clientes_fracionamento: int
    operacoes_atipicas: int
    alertas_regra: int
    alertas_controle: int


# ============================================================================
# App e conexao
# ============================================================================

app = FastAPI(
    title="Mesa de Triagem PLD",
    version="2.0",
    description="Leitura do store da Mesa e transicao de estado dos casos. "
                "Nenhum calculo de regra acontece aqui.",
)

# A tela da Fase 3 roda em localhost numa porta qualquer. Nenhuma origem fora da
# maquina: a API nao tem autenticacao (ver X-Analista) e nao pode ser alcancavel
# de outro lugar enquanto isso for verdade.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Analista"],
)


def conexao():
    """Uma conexao por requisicao, aberta e fechada aqui.

    O caminho e lido de `db.CAMINHO_PADRAO` NA HORA, nao na importacao - e o que
    deixa os testes apontarem a API para um banco temporario.

    Duas recusas antes de abrir, as duas com 503 (o servico existe, o dado nao):
      - store inexistente: `conectar()` criaria um banco VAZIO em silencio, e a
        tela mostraria "0 casos" como se fosse verdade
      - esquema de outra versao: ler colunas que nao existem daria 500 opaco
    Uma leitura nao pode escrever, entao a versao e CONFERIDA, nao aplicada.
    """
    caminho = Path(db.CAMINHO_PADRAO)
    if not caminho.exists():
        raise HTTPException(
            503,
            f"store nao encontrado em {caminho} - rode: python -m mesa.ingestao "
            "&& python -m mesa.regras_run && python -m mesa.triagem",
        )

    conn = db.conectar(caminho, criar_esquema=False, check_same_thread=False)
    try:
        versao = conn.execute("PRAGMA user_version").fetchone()[0]
        if versao != db.VERSAO_ESQUEMA:
            raise HTTPException(
                503,
                f"store na versao de esquema {versao}, a API espera "
                f"{db.VERSAO_ESQUEMA} - reconstrua o store (ver mesa/db.py)",
            )
        yield conn
    finally:
        conn.close()


def _execucao(conn: sqlite3.Connection, execucao_id: int | None) -> int:
    if execucao_id is not None:
        if conn.execute("SELECT 1 FROM execucoes_regras WHERE id = ?", (execucao_id,)).fetchone():
            return execucao_id
        raise HTTPException(404, f"execucao {execucao_id} nao existe")

    linha = conn.execute("SELECT MAX(id) FROM execucoes_regras").fetchone()
    if linha[0] is None:
        raise HTTPException(503, "store sem execucao de regras - rode: python -m mesa.regras_run")
    return int(linha[0])


# O parecer ATUAL de um alerta e sempre o mais recente - o log e append-only, e
# "atual" e uma consulta, nao uma coluna. Mesmo criterio de mesa/pareceres.py.
_PARECER_ATUAL = """
    SELECT id FROM pareceres WHERE alerta_id = a.id
    ORDER BY criado_em DESC, id DESC LIMIT 1
"""

_SELECT_ALERTA = f"""
    SELECT a.id AS alerta_id, a.cliente_id, a.estado, a.origem, a.nivel_risco_regra,
           a.total_sinalizacoes, a.volume_total_brl, a.qtd_operacoes,
           a.analista_id, a.criado_em, a.execucao_id,
           p.nivel_risco AS nivel_risco_agente, ad.fundamentado
    FROM alertas a
    LEFT JOIN pareceres p ON p.id = ({_PARECER_ATUAL})
    LEFT JOIN aderencia ad ON ad.parecer_id = p.id
"""

# Mesma ordem de dados.ranking_clientes_sinalizados(). cliente_id desempata
# porque SQL nao garante estabilidade de sort onde o pandas garante - uma fila
# que reordena entre duas leituras e ruim para o analista.
_ORDEM_FILA = "ORDER BY a.total_sinalizacoes DESC, a.volume_total_brl DESC, a.cliente_id ASC"


def _item_fila(linha: sqlite3.Row) -> ItemFila:
    agente = _normalizar_nivel(linha["nivel_risco_agente"])
    return ItemFila(
        alerta_id=linha["alerta_id"],
        cliente_id=linha["cliente_id"],
        estado=linha["estado"],
        origem=linha["origem"],
        nivel_risco_regra=linha["nivel_risco_regra"],
        nivel_risco_agente=agente,
        concorda=None if agente is None else agente == linha["nivel_risco_regra"],
        fundamentado=None if linha["fundamentado"] is None else bool(linha["fundamentado"]),
        total_sinalizacoes=linha["total_sinalizacoes"],
        volume_total_brl=linha["volume_total_brl"],
        qtd_operacoes=linha["qtd_operacoes"],
        analista_id=linha["analista_id"],
        criado_em=linha["criado_em"],
    )


def _alerta_ou_404(conn: sqlite3.Connection, alerta_id: int) -> sqlite3.Row:
    linha = conn.execute(f"{_SELECT_ALERTA} WHERE a.id = ?", (alerta_id,)).fetchone()
    if linha is None:
        raise HTTPException(404, f"alerta {alerta_id} nao existe")
    return linha


def _parecer_atual_id(conn: sqlite3.Connection, alerta_id: int) -> int | None:
    linha = conn.execute(
        "SELECT id FROM pareceres WHERE alerta_id = ? ORDER BY criado_em DESC, id DESC LIMIT 1",
        (alerta_id,),
    ).fetchone()
    return int(linha[0]) if linha else None


# ---------- cursor da fila ----------
#
# Paginacao por CHAVE (a ultima posicao vista), nao por offset. A fila muda
# enquanto e lida - um analista pega um caso, o estado muda, o caso sai do
# filtro - e offset pularia ou repetiria itens quando isso acontece entre uma
# pagina e outra. A chave e a propria tripla de ordenacao.


def _codificar_cursor(linha: sqlite3.Row) -> str:
    chave = [linha["total_sinalizacoes"], linha["volume_total_brl"], linha["cliente_id"]]
    return base64.urlsafe_b64encode(json.dumps(chave).encode()).decode()


def _decodificar_cursor(cursor: str) -> tuple[int, float, str]:
    try:
        total, volume, cliente = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return int(total), float(volume), str(cliente)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        raise HTTPException(400, "cursor invalido - use o proximo_cursor devolvido pela propria /fila")


# ============================================================================
# Endpoints
# ============================================================================


@app.get("/saude", response_model=Saude)
def saude(conn: sqlite3.Connection = Depends(conexao)):
    """Prova de que a API esta lendo o store, e qual."""
    conta = lambda tabela: conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]  # noqa: E731
    atual = conn.execute("SELECT MAX(id) FROM execucoes_regras").fetchone()[0]
    return Saude(
        status="ok",
        versao_esquema=conn.execute("PRAGMA user_version").fetchone()[0],
        execucao_atual=atual,
        contagens=Contagens(
            operacoes=conta("operacoes"),
            execucoes=conta("execucoes_regras"),
            alertas=conta("alertas"),
            pareceres=conta("pareceres"),
        ),
    )


@app.get("/fila", response_model=Fila)
def fila(
    estado: Estado | None = None,
    origem: Literal["regra", "controle", "todos"] = "regra",
    execucao_id: int | None = None,
    limite: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    conn: sqlite3.Connection = Depends(conexao),
):
    """A fila do analista, na ordem do ranking das regras.

    `origem=regra` por padrao: a fila de trabalho nao mostra cliente sem
    sinalizacao. Os de controle existem para medir falso negativo, nao para
    ocupar o analista - `origem=todos` os inclui.
    """
    execucao_id = _execucao(conn, execucao_id)

    filtros, params = ["a.execucao_id = ?"], [execucao_id]
    if estado is not None:
        filtros.append("a.estado = ?")
        params.append(estado)
    if origem != "todos":
        filtros.append("a.origem = ?")
        params.append(origem)

    total = conn.execute(
        f"SELECT COUNT(*) FROM alertas a WHERE {' AND '.join(filtros)}", params
    ).fetchone()[0]

    if cursor is not None:
        t, v, c = _decodificar_cursor(cursor)
        # igualdade de float e segura aqui: o valor do cursor veio do proprio
        # banco e passou por JSON, cujo repr de float faz ida-e-volta exata
        filtros.append(
            "(a.total_sinalizacoes < ? OR (a.total_sinalizacoes = ? AND "
            "(a.volume_total_brl < ? OR (a.volume_total_brl = ? AND a.cliente_id > ?))))"
        )
        params.extend([t, t, v, v, c])

    linhas = conn.execute(
        f"{_SELECT_ALERTA} WHERE {' AND '.join(filtros)} {_ORDEM_FILA} LIMIT ?",
        [*params, limite + 1],  # +1 para saber se ha proxima pagina sem outra query
    ).fetchall()

    pagina, sobrou = linhas[:limite], len(linhas) > limite
    return Fila(
        execucao_id=execucao_id,
        total=total,
        itens=[_item_fila(l) for l in pagina],
        proximo_cursor=_codificar_cursor(pagina[-1]) if sobrou else None,
    )


@app.get("/alertas/{alerta_id}", response_model=Caso)
def caso(alerta_id: int, conn: sqlite3.Connection = Depends(conexao)):
    """O caso completo numa requisicao so - o que a tela da Fase 3 precisa."""
    alerta = _alerta_ou_404(conn, alerta_id)
    execucao_id, cliente_id = alerta["execucao_id"], alerta["cliente_id"]

    sinalizacoes = [
        Sinalizacao(
            regra=s["regra"], operacao_id=s["operacao_id"], data=s["data"],
            detalhe=json.loads(s["detalhe_json"]),
        )
        for s in conn.execute(
            "SELECT regra, operacao_id, data, detalhe_json FROM sinalizacoes "
            "WHERE execucao_id = ? AND cliente_id = ? ORDER BY regra, data, operacao_id",
            (execucao_id, cliente_id),
        )
    ]
    atipicas = {s.operacao_id for s in sinalizacoes if s.regra == "valor_atipico"}
    dias_frac = {s.data for s in sinalizacoes if s.regra == "fracionamento"}

    operacoes = [
        Operacao(
            id=o["id"], data=o["data"], valor=o["valor"], moeda=o["moeda"],
            valor_brl=o["valor_brl"], canal=o["canal"], tipo=o["tipo"],
            contraparte=o["contraparte"],
            flag_valor_atipico=o["id"] in atipicas,
            em_dia_de_fracionamento=o["data"] in dias_frac,
        )
        for o in conn.execute(
            # operacao sem data vai para o FIM, nao para o topo (padrao do SQLite
            # em ASC): e a menos informativa para quem le o caso em ordem
            "SELECT id, data, valor, moeda, valor_brl, canal, tipo, contraparte "
            "FROM operacoes WHERE cliente_id = ? ORDER BY data IS NULL, data, id",
            (cliente_id,),
        )
    ]

    parecer, aderencia = None, None
    parecer_id = _parecer_atual_id(conn, alerta_id)
    if parecer_id is not None:
        p = conn.execute("SELECT * FROM pareceres WHERE id = ?", (parecer_id,)).fetchone()
        parecer = Parecer(
            parecer_id=p["id"],
            nivel_risco=_normalizar_nivel(p["nivel_risco"]),
            tipologia_suspeita=p["tipologia_suspeita"],
            red_flags=json.loads(p["red_flags_json"]) if p["red_flags_json"] else [],
            justificativa=p["justificativa"],
            erro_parsing=p["erro_parsing"],
            modelo=p["modelo"],
            versao_prompt=p["versao_prompt"],
            origem_registro=p["origem_registro"],
            reaproveitado_de=p["reaproveitado_de"],
            criado_em=p["criado_em"],
        )
        a = conn.execute("SELECT * FROM aderencia WHERE parecer_id = ?", (parecer_id,)).fetchone()
        if a is not None:
            aderencia = Aderencia(
                fundamentado=bool(a["fundamentado"]),
                motivo=a["motivo"],
                valores_confirmados=json.loads(a["valores_confirmados_json"]),
                valores_nao_encontrados=json.loads(a["valores_nao_encontrados_json"]),
                atipicos_incorretos=json.loads(a["atipicos_incorretos_json"]),
                marcas=json.loads(a["marcas_json"]),
            )

    historico = [
        VersaoParecer(
            parecer_id=h["id"], alerta_id=h["alerta_id"],
            nivel_risco=_normalizar_nivel(h["nivel_risco"]),
            origem_registro=h["origem_registro"], reaproveitado_de=h["reaproveitado_de"],
            criado_em=h["criado_em"],
        )
        for h in conn.execute(
            "SELECT id, alerta_id, nivel_risco, origem_registro, reaproveitado_de, criado_em "
            "FROM pareceres WHERE cliente_id = ? ORDER BY criado_em DESC, id DESC",
            (cliente_id,),
        )
    ]

    return Caso(
        alerta=_item_fila(alerta),
        execucao_id=execucao_id,
        sinalizacoes=sinalizacoes,
        parecer=parecer,
        aderencia=aderencia,
        operacoes=operacoes,
        historico_parecer=historico,
        transicoes_permitidas=sorted(d for o, d in TRANSICOES if o == alerta["estado"]),
    )


@app.get("/alertas/{alerta_id}/evidencias", response_model=list[Evidencia])
def evidencias(alerta_id: int, conn: sqlite3.Connection = Depends(conexao)):
    """O que o agente VIU quando decidiu - o retorno de cada ferramenta.

    Alerta sem parecer devolve lista vazia, nao 404: o alerta existe, so ainda
    nao foi triado. 404 fica reservado para o que nao existe.
    """
    _alerta_ou_404(conn, alerta_id)
    parecer_id = _parecer_atual_id(conn, alerta_id)
    if parecer_id is None:
        return []
    return [
        Evidencia(
            ordem=e["ordem"], tool=e["tool"],
            args=json.loads(e["args_json"]), payload=json.loads(e["payload_json"]),
        )
        for e in conn.execute(
            "SELECT ordem, tool, args_json, payload_json FROM evidencias "
            "WHERE parecer_id = ? ORDER BY ordem",
            (parecer_id,),
        )
    ]


@app.post("/alertas/{alerta_id}/estado", response_model=Transicao)
def mudar_estado(
    alerta_id: int,
    corpo: NovoEstado,
    x_analista: str | None = Header(default=None),
    conn: sqlite3.Connection = Depends(conexao),
):
    """Transicao de estado de um caso.

    Quem pede vem no header X-Analista (passo 2.5) - e so no header: o corpo
    carrega apenas o estado de destino. Duas fontes para o mesmo fato e como
    duas verdades comecam.
    """
    analista = (x_analista or "").strip()
    if not analista:
        raise HTTPException(400, "header X-Analista obrigatorio para mudar o estado de um caso")

    alerta = _alerta_ou_404(conn, alerta_id)
    atual, dono = alerta["estado"], alerta["analista_id"]
    destino = corpo.estado
    # nas mensagens, o caso pelo nome que o analista conhece (CLI-028), nao
    # pelo id interno do alerta
    caso_nome = alerta["cliente_id"]

    # Caso ja pego por outro: e o conflito mais comum (tela desatualizada), e
    # merece dizer QUEM pegou, nao "transicao em_analise -> em_analise".
    if atual == "em_analise" and destino == "em_analise":
        quem = "voce" if dono == analista else dono
        raise HTTPException(409, f"o caso {caso_nome} ja esta em analise com {quem}")

    if (atual, destino) not in TRANSICOES:
        permitidas = sorted(d for o, d in TRANSICOES if o == atual)
        motivo = (
            "concluir um caso exige a decisao do analista registrada junto (Fase 4)"
            if destino == "concluido" else
            f"a partir de '{atual}' so e permitido: {permitidas or 'nenhuma transicao'}"
        )
        raise HTTPException(409, f"transicao '{atual}' -> '{destino}' nao permitida: {motivo}")

    # So quem pegou pode devolver. Sem esta regra, qualquer analista com a
    # pagina aberta devolveria para a fila um caso em analise por outro, e o dono
    # nem ficaria sabendo. (Nao e controle de acesso - X-Analista e so
    # identificacao, ver 2.5 -, mas impede a interferencia por engano. Reatribuir
    # caso de outro e papel de supervisor, que depende de autenticacao de verdade.)
    if atual == "em_analise" and destino == "triado" and dono and dono != analista:
        raise HTTPException(
            409, f"o caso {caso_nome} esta em analise com {dono}; so quem pegou o caso pode devolve-lo"
        )

    # Pegar o caso grava quem pegou; devolver para a fila libera o dono.
    novo_analista = analista if destino == "em_analise" else None

    # Compare-and-set: so atualiza se o estado AINDA for o que acabamos de ler.
    # Ler-e-depois-escrever deixaria dois analistas pegarem o mesmo caso ao mesmo
    # tempo; com a condicao no WHERE, o banco garante que so um vence.
    # O dono lido tambem entra na condicao (`IS` compara NULL com seguranca):
    # se outro analista pegou e devolveu o caso entre a leitura e a escrita, o
    # estado voltou ao mesmo, mas o caso ja nao e o que foi lido.
    cur = conn.execute(
        "UPDATE alertas SET estado = ?, analista_id = ? "
        "WHERE id = ? AND estado = ? AND analista_id IS ?",
        (destino, novo_analista, alerta_id, atual, dono),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(
            409, f"o caso {caso_nome} mudou de estado durante a requisicao - recarregue e tente de novo"
        )

    return Transicao(
        alerta_id=alerta_id, estado_anterior=atual, estado=destino, analista_id=novo_analista,
    )


_SELECT_EXECUCAO = """
    SELECT e.*,
           (SELECT COUNT(*) FROM alertas WHERE execucao_id = e.id AND origem = 'regra') AS alertas_regra,
           (SELECT COUNT(*) FROM alertas WHERE execucao_id = e.id AND origem = 'controle') AS alertas_controle
    FROM execucoes_regras e
"""


def _execucao_modelo(linha: sqlite3.Row) -> Execucao:
    return Execucao(
        id=linha["id"], lote_id=linha["lote_id"], versao_regras=linha["versao_regras"],
        # os parametros GRAVADOS naquela execucao - nao os de dados.py hoje. E a
        # resposta para "com que limiar este alerta nasceu?"
        parametros=json.loads(linha["parametros_json"]),
        executado_em=linha["executado_em"],
        operacoes_avaliadas=linha["operacoes_avaliadas"],
        clientes_fracionamento=linha["clientes_fracionamento"],
        operacoes_atipicas=linha["operacoes_atipicas"],
        alertas_regra=linha["alertas_regra"],
        alertas_controle=linha["alertas_controle"],
    )


@app.get("/execucoes", response_model=list[Execucao])
def execucoes(conn: sqlite3.Connection = Depends(conexao)):
    return [_execucao_modelo(l) for l in conn.execute(f"{_SELECT_EXECUCAO} ORDER BY e.id DESC")]


@app.get("/execucoes/{execucao_id}", response_model=Execucao)
def execucao(execucao_id: int, conn: sqlite3.Connection = Depends(conexao)):
    linha = conn.execute(f"{_SELECT_EXECUCAO} WHERE e.id = ?", (execucao_id,)).fetchone()
    if linha is None:
        raise HTTPException(404, f"execucao {execucao_id} nao existe")
    return _execucao_modelo(linha)


# ============================================================================
# Fase 3 - a tela
# ============================================================================


@app.get("/", include_in_schema=False)
def raiz():
    return RedirectResponse("/app/")


# Montado por ULTIMO: um mount captura o prefixo inteiro, e registrado antes das
# rotas poderia sombrear alguma. html=True serve o index.html em /app/.
app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="tela")
