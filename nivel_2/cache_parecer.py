"""Cache de pareceres do agente, indexado por hash da entrada.

O problema que isto resolve: rodar o agente duas vezes para o MESMO cliente, com os
MESMOS dados, pode devolver nivel_risco diferente - medimos isso na pratica (ver
docs/DECISOES.md, secao de confronto: 50% -> 30% de concordancia entre duas execucoes
do mesmo codigo). Em PLD isso e problema de auditoria: um parecer de risco precisa ser
reproduzivel, senao "por que este cliente foi classificado como alto risco" nao tem
resposta estavel.

A solucao NAO e forcar o LLM a ser deterministico (temperature=0 ajuda mas nao garante).
E parar de tratar "gerar parecer" como uma funcao que sempre recalcula, e passar a tratar
como um mapa chave->valor: a chave e um hash de tudo que deveria influenciar a resposta.
Se a entrada nao mudou, o parecer nao muda - ele e reaproveitado, nao regerado.

O que entra no hash e a decisao de design que importa:
  - cliente_id, flags deterministicas e o snapshot dos dados do cliente (para invalidar
    o cache se a base mudar)
  - modelo usado (openai/gpt-oss-120b hoje != se trocarmos de modelo amanha)
  - versao do prompt (SYSTEM_PROMPT) - mudar o prompt tem que gerar parecer novo
Deliberadamente NAO entra: timestamp, tokens consumidos, latencia - isso e metadado da
EXECUCAO, nao da ENTRADA, e nao deveria fazer dois hashes diferentes pro mesmo caso.
"""
import hashlib
import json
from pathlib import Path

CACHE_PATH = Path(__file__).resolve().parent.parent / "outputs" / "cache_pareceres.json"

VERSAO_PROMPT = "v1-2026-08-25"  # bump manual quando SYSTEM_PROMPT mudar de verdade


def calcular_hash(cliente_id: str, flags: dict, dados_cliente: dict, modelo: str) -> str:
    """dados_cliente e o retorno de historico_cliente() - resumo agregado, nao a base
    inteira. Suficiente para invalidar o cache se os numeros daquele cliente mudarem
    (nova operacao, correcao de dado), sem precisar hashear a base de 322 operacoes
    inteira a cada chamada."""
    payload = {
        "cliente_id": cliente_id,
        "flags": flags,
        "dados_cliente": dados_cliente,
        "modelo": modelo,
        "versao_prompt": VERSAO_PROMPT,
    }
    bruto = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:16]


class CacheParecer:
    """Store simples em JSON. Para o volume desta entrega (30 clientes, no maximo)
    um arquivo basta - nao ha justificativa para SQLite aqui. Ver DECISOES.md sobre
    quando isso deixaria de ser verdade (volume real de um banco)."""

    def __init__(self, caminho: Path = CACHE_PATH):
        self.caminho = caminho
        self._dados: dict[str, dict] = {}
        if caminho.exists():
            self._dados = json.loads(caminho.read_text(encoding="utf-8"))

    def obter(self, hash_entrada: str) -> dict | None:
        return self._dados.get(hash_entrada)

    def salvar(self, hash_entrada: str, resultado: dict) -> None:
        self._dados[hash_entrada] = resultado
        self.caminho.parent.mkdir(exist_ok=True)
        self.caminho.write_text(
            json.dumps(self._dados, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self._dados)
