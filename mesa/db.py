"""Conexao e esquema do store da Mesa de Triagem.

Por que SQLite e nao "so continuar com os JSONs": as ferramentas do agente
reliam a base inteira para responder sobre um cliente, e nada do que o pipeline
produzia sobrevivia a um `rm outputs/`. O store resolve as duas coisas de uma
vez - indice por cliente/data, e persistencia com trilha de auditoria.

Por que SQLite e nao Postgres agora: o volume desta base (317 operacoes) nao
justifica um servico, e todo o DDL em esquema.sql foi escrito portavel de
proposito. A troca fica sendo uma edicao localizada, nao uma reescrita.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_PADRAO = RAIZ / "outputs" / "mesa.db"
ESQUEMA_SQL = Path(__file__).resolve().parent / "esquema.sql"

# Versao do esquema aplicado, gravada em PRAGMA user_version. Serve para detectar
# um banco velho antes de escrever nele, em vez de descobrir na primeira query
# que uma coluna nao existe. Bump manual a cada alteracao de esquema.
#
# NAO ha migracao automatica ainda (esta na Fase 5). Enquanto o store so contem
# dado sintetico reprocessavel, alterar o esquema significa apagar o banco e
# reingerir - barato e honesto. No dia em que houver decisao de analista gravada
# aqui, isso deixa de ser aceitavel e a migracao vira pre-requisito.
VERSAO_ESQUEMA = 4


def agora_utc() -> str:
    """Timestamp ISO-8601 em UTC, o formato usado em todas as colunas *_em.

    UTC e nao horario local porque um registro de decisao de risco que muda de
    significado conforme o fuso de quem gravou nao serve de trilha de auditoria.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conectar(caminho: Path | str = CAMINHO_PADRAO, criar_esquema: bool = True) -> sqlite3.Connection:
    """Abre (e cria, se preciso) o banco com os PRAGMAs que o resto do codigo assume.

    `foreign_keys=ON` nao e opcional aqui: o SQLite deixa as FKs DESLIGADAS por
    padrao, por compatibilidade historica, e por conexao - declarar REFERENCES no
    DDL sem ligar o PRAGMA e ter integridade referencial so no comentario. Como e
    por conexao, tem que ser feito aqui e nao no esquema.
    """
    caminho = Path(caminho)
    em_memoria = str(caminho) == ":memory:"
    if not em_memoria:
        caminho.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not em_memoria:
        # WAL: leitor nao bloqueia escritor. Importa quando o worker de triagem
        # (Fase 1.7) estiver gravando parecer enquanto a API le a fila.
        # Nao se aplica a bancos em memoria - por isso o guard, e nao um try/except
        # que engoliria um erro real de disco.
        conn.execute("PRAGMA journal_mode = WAL")

    if criar_esquema:
        aplicar_esquema(conn)
    return conn


def aplicar_esquema(conn: sqlite3.Connection) -> None:
    """Idempotente: todo o DDL usa IF NOT EXISTS, entao rodar em banco ja criado
    nao faz nada. E o que permite chamar `conectar()` sem saber se o banco existe."""
    versao_atual = conn.execute("PRAGMA user_version").fetchone()[0]
    if versao_atual > VERSAO_ESQUEMA:
        raise RuntimeError(
            f"banco na versao de esquema {versao_atual}, codigo espera {VERSAO_ESQUEMA} "
            "- este codigo e mais antigo que o banco; atualize o codigo em vez de "
            "escrever num esquema que ele nao entende"
        )
    conn.executescript(ESQUEMA_SQL.read_text(encoding="utf-8"))
    conn.execute(f"PRAGMA user_version = {VERSAO_ESQUEMA}")
    conn.commit()
