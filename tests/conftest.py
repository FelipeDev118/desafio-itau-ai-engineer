"""Fixtures compartilhadas. Os módulos de nivel_2 usam imports "soltos" (ex.:
`from dados import ...`), assumindo que rodam com nivel_2/ no sys.path — é como
funcionam quando executados como script (`python nivel_2/agente.py`). Aqui replicamos
isso manualmente para o pytest, sem alterar o código de produção.
"""
import json
import os
import sys
from pathlib import Path

# agente.py instancia Groq(api_key=os.environ["GROQ_API_KEY"]) na importação do módulo.
# Os testes mockam a chamada de API (nunca falam com a rede), então uma chave falsa
# basta — mas precisa existir ANTES do primeiro `import agente`. setdefault não
# sobrescreve uma chave real já exportada no shell.
os.environ.setdefault("GROQ_API_KEY", "test-key-nao-real")

RAIZ = Path(__file__).resolve().parent.parent
NIVEL_2 = RAIZ / "nivel_2"
if str(NIVEL_2) not in sys.path:
    sys.path.insert(0, str(NIVEL_2))

# A raiz tambem, para `from mesa import ...`. Sem isto os testes do pacote mesa/
# so passam via `python -m pytest` (que insere o CWD no sys.path por conta
# propria) e quebram com um `pytest tests/` direto - dependencia silenciosa de
# COMO o teste foi invocado, que ja pegou uma vez.
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def escrever_dataset(tmp_path, operacoes, taxa=5.4):
    """Escreve um dataset sintético no formato de dados/dados_nivel_2.json e devolve
    o caminho — exercita carregar_e_limpar() de ponta a ponta, não só as regras."""
    caminho = tmp_path / "dataset.json"
    caminho.write_text(
        json.dumps({"taxa_cambio_usd_brl": taxa, "operacoes": operacoes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return caminho


def op(id_, cliente_id, data, valor, moeda="BRL", **kw):
    """Monta uma operação com os campos mínimos exigidos pelo schema, com defaults
    para os campos que as regras determinísticas não usam (canal/tipo/contraparte)."""
    base = {
        "id": id_,
        "cliente_id": cliente_id,
        "data": data,
        "valor": valor,
        "moeda": moeda,
        "canal": "pix",
        "tipo": "pagamento",
        "contraparte": "Fornecedor Teste",
        "observacao": "",
    }
    base.update(kw)
    return base
