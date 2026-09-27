"""Passo 1.7 - o worker que consome a fila de alertas.

E o `lote.py` da entrega virado do avesso. Aquele itera um DataFrame e escreve
arquivos; este consome uma FILA com estado e escreve registros. A diferenca
pratica aparece quando algo da errado no meio: o lote reprocessa tudo do zero
(pagando as chamadas de API de novo), o worker retoma de onde parou.

A garantia de retomada nao vem de transacao esperta: vem de o proprio store
saber o que ja foi feito. Antes de chamar o LLM para um alerta, o worker verifica
se aquele alerta ja tem parecer. Se tem, so ajusta o estado. Um kill -9 no meio
do lote custa, no maximo, o alerta que estava em voo.
"""
import sqlite3
import sys
import time
from dataclasses import dataclass, field

import mesa  # noqa: F401
from agente import rodar_agente
from observabilidade import Coletor
from verificacao_aderencia import verificar

from mesa import regras_run, repositorio
from mesa.db import agora_utc, conectar
from mesa.pareceres import RepositorioPareceres

# Espacamento entre chamadas reais de API: o free tier do Groq limita tokens por
# minuto. Mesmo valor que lote.py usa, pelo mesmo motivo.
PAUSA_RATE_LIMIT_S = 8

# Como o worker aparece na trilha de transicoes: processo, nao pessoa.
ATOR_TRIAGEM = "sistema:triagem"


@dataclass
class ResultadoTriagem:
    execucao_id: int
    triados: int = 0
    reaproveitados: int = 0
    ja_tinham_parecer: int = 0
    chamadas_api: int = 0
    fundamentados: int = 0
    nao_fundamentados: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"execucao {self.execucao_id}: {self.triados} alertas triados "
            f"({self.reaproveitados} reaproveitados, {self.ja_tinham_parecer} ja tinham "
            f"parecer, {self.chamadas_api} chamadas de API) | "
            f"aderencia: {self.fundamentados}/{self.triados} fundamentados"
        )


def _execucao_mais_recente(conn: sqlite3.Connection) -> int:
    linha = conn.execute("SELECT id FROM execucoes_regras ORDER BY id DESC LIMIT 1").fetchone()
    if linha is None:
        raise RuntimeError("nenhuma execucao de regras no store - rode: python -m mesa.regras_run")
    return int(linha[0])


def montar_flags_do_alerta(conn: sqlite3.Connection, alerta: sqlite3.Row) -> dict:
    """As mesmas flags que dados.montar_flags() produz, lidas do store.

    Tem que ser as MESMAS, chave por chave: elas entram no hash do parecer, e um
    dict diferente aqui significaria hash diferente - o que faria o worker
    ignorar todo o historico ja importado e regerar 30 pareceres a peso de
    chamada de API. Ha um teste comparando as duas montagens cliente a cliente.
    """
    flags = {
        "flag_fracionamento": bool(alerta["sinalizacoes_fracionamento"]),
        "flag_valor_atipico": bool(alerta["sinalizacoes_valor_atipico"] > 0),
    }
    if flags["flag_fracionamento"]:
        flags["datas_fracionamento"] = regras_run.datas_fracionamento_do_store(
            conn, alerta["cliente_id"], alerta["execucao_id"]
        )
    return flags


def fila(conn: sqlite3.Connection, execucao_id: int, estado: str = "novo",
         apenas_regra: bool = False) -> list[sqlite3.Row]:
    """A fila na ordem do ranking. `apenas_regra=True` deixa de fora os alertas
    de controle (clientes sem sinalizacao) - util para o analista, mas o worker
    tria todos por padrao, porque sao eles que medem o falso negativo."""
    sql = (
        "SELECT * FROM alertas WHERE execucao_id = ? AND estado = ? "
        + ("AND origem = 'regra' " if apenas_regra else "")
        + "ORDER BY total_sinalizacoes DESC, volume_total_brl DESC, cliente_id ASC"
    )
    return conn.execute(sql, (execucao_id, estado)).fetchall()


def triar(conn: sqlite3.Connection, execucao_id: int | None = None,
          limite: int | None = None, pausa_s: float = PAUSA_RATE_LIMIT_S,
          verbose: bool = True) -> ResultadoTriagem:
    execucao_id = execucao_id or _execucao_mais_recente(conn)
    repo = RepositorioPareceres(conn)
    coletor = Coletor()
    df = repositorio.operacoes_df(conn)
    from dados import aplicar_regras

    df_regras = aplicar_regras(df)

    resultado_geral = ResultadoTriagem(execucao_id=execucao_id)
    pendentes = fila(conn, execucao_id)
    if limite is not None:
        pendentes = pendentes[:limite]

    for alerta in pendentes:
        cliente_id = alerta["cliente_id"]
        alerta_id = alerta["id"]

        # Retomada: se este alerta ja tem parecer, uma execucao anterior foi
        # interrompida DEPOIS de gravar e ANTES de mudar o estado. Regerar aqui
        # custaria uma chamada de API para chegar (provavelmente) ao mesmo texto.
        existente = repo.do_alerta(alerta_id)
        if existente is not None:
            _concluir(conn, alerta_id)
            resultado_geral.ja_tinham_parecer += 1
            resultado_geral.triados += 1
            if _fundamentado(conn, existente["parecer_id"]):
                resultado_geral.fundamentados += 1
            continue

        flags = montar_flags_do_alerta(conn, alerta)
        resultado = rodar_agente(cliente_id, flags, coletor=coletor, cache=repo)

        if resultado["cache_hit"]:
            # Ha parecer para esta entrada, mas gerado para outro alerta (ou
            # importado do cache antigo). Vincular sem regerar - passo 1.5.
            parecer_id = repo.reaproveitar(resultado["hash_entrada"], alerta_id)
            resultado_geral.reaproveitados += 1
            marcador = " (reaproveitado, sem chamada de API)"
        else:
            # rodar_agente ja gravou via cache=repo; recuperamos o id da linha
            parecer_id = repo.do_alerta(alerta_id)["parecer_id"]
            resultado_geral.chamadas_api += 1
            marcador = ""

        if resultado.get("parecer"):
            aderencia = verificar(cliente_id, resultado["parecer"]["justificativa"], df_regras)
            repo.gravar_aderencia(parecer_id, {
                "fundamentado": aderencia.fundamentado,
                "motivo": aderencia.motivo,
                "valores_confirmados": aderencia.valores_confirmados,
                "valores_nao_encontrados": aderencia.valores_nao_encontrados,
                "atipicos_incorretos": aderencia.atipicos_incorretos,
                "marcas": aderencia.marcas,
            })
            if aderencia.fundamentado:
                resultado_geral.fundamentados += 1
            else:
                resultado_geral.nao_fundamentados.append(f"{cliente_id}: {aderencia.motivo}")
        else:
            repo.gravar_aderencia(parecer_id, {
                "fundamentado": False, "motivo": "sem parecer para verificar",
            })
            resultado_geral.nao_fundamentados.append(f"{cliente_id}: sem parecer")

        _concluir(conn, alerta_id)
        resultado_geral.triados += 1
        if verbose:
            print(f"  {cliente_id}: {(resultado.get('parecer') or {}).get('nivel_risco', '-')}"
                  f"{marcador}")

        if not resultado["cache_hit"] and pausa_s:
            time.sleep(pausa_s)

    # As chamadas do Coletor sao gravadas com o parecer a que pertencem.
    for cliente_id, chamadas in _agrupar_por_cliente(coletor).items():
        alerta_id = _alerta_do_cliente(conn, execucao_id, cliente_id)
        parecer = repo.do_alerta(alerta_id) if alerta_id else None
        if parecer:
            repo.gravar_chamadas(parecer["parecer_id"], chamadas)

    return resultado_geral


def _concluir(conn: sqlite3.Connection, alerta_id: int) -> None:
    """'triado' = o agente passou por aqui. NAO e 'concluido': quem conclui um
    caso e o analista (Fase 4), nunca o worker.

    So sai de 'novo'. Um analista pode ter pegado o caso enquanto o agente
    rodava (novo -> em_analise pela API); um UPDATE sem condicao jogaria o caso
    de volta para 'triado' com o analista ainda gravado como dono - um caso na
    fila e "de alguem" ao mesmo tempo. O parecer continua gravado e vinculado;
    so o estado nao e tocado.

    A transicao entra na trilha (Fase 4) na mesma transacao do UPDATE."""
    cur = conn.execute(
        "UPDATE alertas SET estado = 'triado' WHERE id = ? AND estado = 'novo'", (alerta_id,)
    )
    if cur.rowcount == 1:
        conn.execute(
            "INSERT INTO transicoes (alerta_id, estado_anterior, estado_novo, ator, ator_tipo, "
            "registrado_em) VALUES (?, 'novo', 'triado', ?, 'sistema', ?)",
            (alerta_id, ATOR_TRIAGEM, agora_utc()),
        )
    conn.commit()


def _fundamentado(conn: sqlite3.Connection, parecer_id: int) -> bool:
    linha = conn.execute(
        "SELECT fundamentado FROM aderencia WHERE parecer_id = ?", (parecer_id,)
    ).fetchone()
    return bool(linha[0]) if linha else False


def _agrupar_por_cliente(coletor: Coletor) -> dict[str, list]:
    por_cliente: dict[str, list] = {}
    for chamada in coletor.chamadas:
        por_cliente.setdefault(chamada.cliente_id, []).append(chamada)
    return por_cliente


def _alerta_do_cliente(conn: sqlite3.Connection, execucao_id: int, cliente_id: str) -> int | None:
    linha = conn.execute(
        "SELECT id FROM alertas WHERE execucao_id = ? AND cliente_id = ?",
        (execucao_id, cliente_id),
    ).fetchone()
    return int(linha[0]) if linha else None


if __name__ == "__main__":
    limite = int(sys.argv[sys.argv.index("--limite") + 1]) if "--limite" in sys.argv else None
    with conectar() as conn:
        resultado = triar(conn, limite=limite)
    print(f"\n{resultado}")
    for falha in resultado.nao_fundamentados:
        print(f"  nao fundamentado - {falha}")
