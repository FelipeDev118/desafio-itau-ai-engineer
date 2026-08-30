"""Verificacao de aderencia: confere se os valores em R$ que a justificativa do LLM cita
existem de fato nos dados do cliente, antes de aceitar o parecer como fundamentado.

Motivacao concreta, nao hipotetica: numa execucao anterior, o parecer de CLI-005 citou
"a operacao de 2024-05-07 (R$ 409,16)" como a operacao atipica - so que R$ 409,16 esta
ABAIXO da mediana do cliente (nao e a operacao sinalizada pela Regra 2) e o ano estava
errado (2024, nao 2026). O texto era bem escrito e soava tecnico. Um analista humano
lendo so a justificativa nao teria como perceber o erro sem reabrir a base.

Esta verificacao automatiza exatamente essa conferencia: extrai todo valor em R$ citado
na justificativa e confere contra os dados REAIS do cliente - operacoes individuais,
somas por data (relevante para fracionamento) e agregados do cliente (soma, media,
mediana). Deliberadamente SEM LLM: usar um modelo para auditar outro reintroduziria o
mesmo problema que estamos tentando pegar.

O que isto NAO faz: nao entende o CONTEXTO da citacao alem de um filtro simples de
limiares textuais ("superior a R$X" nao e uma transacao, e um qualificador). E uma
verificacao de EXISTENCIA, nao de RACIOCINIO - ainda assim, e suficiente para pegar o
caso CLI-005: la, o numero citado nem existia como operacao nem como agregado do
cliente. Ver DECISOES.md para a distincao entre isso e um "juiz" completo.

Duas categorias de falso positivo encontradas ao validar contra dados reais, e como
foram tratadas:
  1. Soma de operacoes de UMA DATA especifica (ex.: "4 operacoes em 26/05, totalizando
     R$71.297,68") - nao e a soma total do cliente, e a soma do dia. Adicionado como
     referencia extra quando o cliente tem flag_fracionamento.
  2. Limiares textuais ("valores superiores a R$3.000") - nao sao uma transacao citada,
     sao uma qualificacao vaga. Filtrados por palavras-gatilho antes do valor.
"""
import re
from dataclasses import dataclass, field

import pandas as pd

# R$ 14.326,29 | R$14.326,29 | R$ 7.330 | R$312,54 | R$ 88.750,8
PADRAO_VALOR_RS = re.compile(
    r"R\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)"
)

# O modelo tambem escreve valor sem "R$", com sufixo "BRL" - e formato numerico
# inconsistente entre chamadas: "21.261,01 BRL" (formato BR) ou "5016.62 BRL" (ponto
# como decimal, sem separador de milhar). Captura ampla; o parsing decide o formato.
PADRAO_VALOR_BRL_SUFIXO = re.compile(
    r"(\d[\d.,]*\d|\d+)\s*BRL\b", re.IGNORECASE
)

# Palavras que, aparecendo logo antes do valor, indicam limiar/qualificador, nao uma
# transacao ou agregado especifico sendo citado ("valores superiores a R$3.000").
QUALIFICADORES = re.compile(
    r"(superior(?:es)?\s+a|acima\s+de|abaixo\s+de|menos\s+de|mais\s+de|"
    r"pr[oó]xim[oa]s?\s+(?:a|de)|cerca\s+de|aproximadamente|em\s+torno\s+de|"
    r"at[ée])\s*$",
    re.IGNORECASE,
)

TOLERANCIA_R = 0.5  # cobre "R$7.330" citando 7330.00 sem casas decimais
JANELA_CONTEXTO = 25  # caracteres antes do valor, para checar qualificador

# Verificacao de CONTEXTO (nao so de existencia): quando o texto liga um valor a palavra
# "atipic*", esse valor tem que ser de uma operacao com flag_valor_atipico=True. E o erro
# do CLI-005: R$409,16 EXISTE na base (entao passa na checagem de existencia), mas foi
# citado como a operacao atipica quando esta abaixo da mediana e nao tem a flag.
JANELA_ATIPICO = 160  # caracteres ao redor do valor onde procuramos a palavra
PADRAO_ATIPICO = re.compile(r"at[ií]pic", re.IGNORECASE)


def _parsear_valor_brl(bruto: str) -> float:
    """Lida com os dois formatos que o modelo produz, sem assumir um so:
      '14.326,29' -> 14326.29   (BR: virgula decimal, ponto de milhar)
      '7.330'     -> 7330.0     (BR: ponto de milhar, sem decimais)
      '5016.62'   -> 5016.62    (US: ponto decimal, 2 casas no fim, sem virgula)
    A regra: se ha virgula, o formato e BR. Se nao ha virgula mas ha um ponto seguido
    de exatamente 2 digitos no fim, tratamos como decimal americano."""
    if "," in bruto:
        return float(bruto.replace(".", "").replace(",", "."))
    if re.search(r"\.\d{2}$", bruto):
        return float(bruto)
    return float(bruto.replace(".", ""))


def extrair_valores(texto: str) -> list[dict]:
    """Retorna [{'valor': float, 'e_limiar': bool}, ...] - separa valores citados como
    transacao/agregado de valores citados como limiar textual.

    Deduplica por posicao para nao contar duas vezes um valor que casasse nos dois
    padroes (ex.: "R$ 100,00 BRL")."""
    achados: dict[int, dict] = {}
    for padrao in (PADRAO_VALOR_RS, PADRAO_VALOR_BRL_SUFIXO):
        for m in padrao.finditer(texto):
            try:
                valor = _parsear_valor_brl(m.group(1))
            except ValueError:
                continue  # captura ampla do padrao BRL pode pegar lixo tipo "1.2.3"
            contexto_antes = texto[max(0, m.start() - JANELA_CONTEXTO):m.start()]
            janela = texto[max(0, m.start() - JANELA_ATIPICO):m.end() + JANELA_ATIPICO]
            achados[m.start(1)] = {
                "valor": valor,
                "e_limiar": bool(QUALIFICADORES.search(contexto_antes)),
                "citado_como_atipico": bool(PADRAO_ATIPICO.search(janela)),
            }
    return [achados[k] for k in sorted(achados)]


@dataclass
class Aderencia:
    cliente_id: str
    valores_citados: list[float] = field(default_factory=list)
    valores_limiar_ignorados: list[float] = field(default_factory=list)
    valores_confirmados: list[dict] = field(default_factory=list)   # {"valor":, "fonte":}
    valores_nao_encontrados: list[float] = field(default_factory=list)
    # valores que EXISTEM na base mas foram citados como "atipicos" sem ter a flag
    atipicos_incorretos: list[dict] = field(default_factory=list)
    fundamentado: bool = True
    motivo: str = ""


def _referencias_validas(cliente_id: str, df: pd.DataFrame) -> dict[str, float]:
    """Todo numero que seria legitimo citar sobre este cliente: cada operacao
    individual, os agregados do cliente inteiro, e a soma de cada data que tem 3+
    operacoes (candidata a ser citada por fracionamento, com ou sem a flag ter
    disparado oficialmente - o agente pode descrever o padrao mesmo perto do limite)."""
    sub = df[df["cliente_id"] == cliente_id]
    refs: dict[str, float] = {}

    for _, row in sub.iterrows():
        refs[f"operacao {row['id']}"] = round(float(row["valor_brl"]), 2)

    refs["volume_total_cliente"] = round(float(sub["valor_brl"].sum()), 2)
    refs["media_cliente"] = round(float(sub["valor_brl"].mean()), 2)
    refs["mediana_cliente"] = round(float(sub["valor_brl"].median()), 2)

    com_data = sub[sub["data_valida"]]
    for data, grupo in com_data.groupby("data"):
        if len(grupo) >= 2:  # qualquer agrupamento por data que faca sentido citar
            refs[f"soma_do_dia_{data.strftime('%Y-%m-%d')}"] = round(
                float(grupo["valor_brl"].sum()), 2
            )

    return refs


def verificar(cliente_id: str, justificativa: str, df: pd.DataFrame) -> Aderencia:
    """df ja deve estar limpo/com regras aplicadas (saida de aplicar_regras)."""
    referencias = _referencias_validas(cliente_id, df)

    extraidos = extrair_valores(justificativa)
    verificaveis = [e for e in extraidos if not e["e_limiar"]]
    citados = [e["valor"] for e in verificaveis]
    limiares = [e["valor"] for e in extraidos if e["e_limiar"]]

    resultado = Aderencia(
        cliente_id=cliente_id, valores_citados=citados, valores_limiar_ignorados=limiares
    )

    sub = df[df["cliente_id"] == cliente_id]
    valores_atipicos_reais = set(
        sub.loc[sub["flag_valor_atipico"], "valor_brl"].round(2)
    )

    for item in verificaveis:
        v = item["valor"]
        fonte = next(
            (nome for nome, ref in referencias.items() if abs(v - ref) <= TOLERANCIA_R),
            None,
        )
        if not fonte:
            resultado.valores_nao_encontrados.append(v)
            continue

        resultado.valores_confirmados.append({"valor": v, "fonte": fonte})

        # existe, mas foi citado como atipico sendo que nao e?
        if item["citado_como_atipico"] and fonte.startswith("operacao"):
            e_atipico_real = any(
                abs(v - real) <= TOLERANCIA_R for real in valores_atipicos_reais
            )
            if not e_atipico_real:
                resultado.atipicos_incorretos.append({"valor": v, "fonte": fonte})

    if not citados:
        resultado.fundamentado = False
        resultado.motivo = "nenhum valor verificavel citado (so limiares, ou nenhum R$ no texto)"
    elif resultado.valores_nao_encontrados:
        resultado.fundamentado = False
        resultado.motivo = (
            f"{len(resultado.valores_nao_encontrados)}/{len(citados)} valores citados "
            "nao correspondem a nenhuma operacao, soma diaria ou agregado do cliente"
        )
    elif resultado.atipicos_incorretos:
        resultado.fundamentado = False
        valores = [d["valor"] for d in resultado.atipicos_incorretos]
        resultado.motivo = (
            f"valores citados como atipicos que NAO tem flag_valor_atipico: {valores} "
            "- o numero existe na base, mas o parecer o usa no contexto errado"
        )
    else:
        resultado.motivo = f"{len(citados)}/{len(citados)} valores citados conferem"

    return resultado
