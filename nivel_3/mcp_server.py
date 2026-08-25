"""Nivel 3 - Trilha B: servidor MCP local (stdio) que expoe as ferramentas do Nivel 2.

O ponto da trilha e separar QUEM EXPOE a ferramenta de QUEM A CONSOME. Aqui o servidor
importa as mesmas funcoes de nivel_2/tools.py - a logica de negocio nao e reescrita nem
duplicada; o que muda e o transporte: em vez de o agente chamar a funcao Python direto,
ele fala com este processo por stdio, no protocolo MCP.

O servidor deriva nome, descricao e schema de cada ferramenta da propria assinatura e do
docstring das funcoes de nivel_2/tools.py - por isso as descricoes que o cliente descobre
em runtime sao as mesmas que documentam o codigo, sem uma segunda copia para manter.

Rodar manualmente (fica aguardando mensagens MCP em stdin):
    python nivel_3/mcp_server.py

Na pratica quem o executa e o cliente (nivel_3/agente_mcp.py), que sobe este arquivo como
subprocesso. Ver docs/ARQUITETURA.md.
"""
import json
import sys
from pathlib import Path

# tools.py e dados.py vivem em nivel_2/ e sao importados sem duplicacao de logica
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nivel_2"))

from mcp.server import MCPServer

from tools import historico_cliente, operacoes_do_dia, perfil_canal

server = MCPServer(name="pld-triagem", version="1.0.0")


@server.tool(name="historico_cliente")
def historico_cliente_mcp(cliente_id: str) -> str:
    """Resumo agregado das operacoes do cliente (volume, contagem, flags)."""
    return json.dumps(historico_cliente(cliente_id), ensure_ascii=False, default=str)


@server.tool(name="operacoes_do_dia")
def operacoes_do_dia_mcp(cliente_id: str, data: str) -> str:
    """Detalhe das operacoes do cliente em uma data especifica (formato YYYY-MM-DD)."""
    return json.dumps(operacoes_do_dia(cliente_id, data), ensure_ascii=False, default=str)


@server.tool(name="perfil_canal")
def perfil_canal_mcp(cliente_id: str) -> str:
    """Distribuicao de uso de canais do cliente."""
    return json.dumps(perfil_canal(cliente_id), ensure_ascii=False, default=str)


if __name__ == "__main__":
    server.run(transport="stdio")
