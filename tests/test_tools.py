"""Teste da materialização de nivel_2/tools.py: _df() deve ler e tratar o JSON uma
única vez por processo, não a cada chamada de ferramenta (ver docstring de _df())."""
import tools


def test_df_e_a_mesma_instancia_entre_chamadas():
    tools._df.cache_clear()
    assert tools._df() is tools._df()
    tools._df.cache_clear()


def test_carregar_e_limpar_roda_uma_unica_vez_para_varias_chamadas_de_ferramenta(monkeypatch):
    tools._df.cache_clear()
    chamadas = {"n": 0}
    original = tools.carregar_e_limpar

    def contando(*args, **kwargs):
        chamadas["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(tools, "carregar_e_limpar", contando)

    tools.historico_cliente("CLI-014")
    tools.operacoes_do_dia("CLI-014", "2026-03-06")
    tools.perfil_canal("CLI-014")

    assert chamadas["n"] == 1
    tools._df.cache_clear()
