// Tela da Mesa de Triagem - DOM e rede. A logica pura (e testada) fica em
// logica.js; aqui so se busca na API e se desenha.
//
// SEGURANCA: a justificativa do parecer e texto gerado por um LLM. Nada que
// venha da API e inserido como HTML - tudo entra como no de texto, pela funcao
// h() abaixo. Um innerHTML com o texto do modelo transformaria uma saida
// malformada (ou maliciosa) em codigo rodando no navegador do analista.

import {
  alvoDaFonte, explicacaoDaMarca, formatarBRL, formatarData, formatarMomento,
  operacaoEhAlvo, rotuloDaFonte, ROTULO_ESTADO, ROTULO_TRANSICAO, segmentar,
  situacaoDaFila,
} from "./logica.js";

const $ = (seletor, raiz = document) => raiz.querySelector(seletor);

function h(tag, atributos, ...filhos) {
  const el = document.createElement(tag);
  for (const [chave, valor] of Object.entries(atributos ?? {})) {
    if (valor == null || valor === false) continue;
    if (chave === "class") el.className = valor;
    else if (chave === "dataset") Object.assign(el.dataset, valor);
    else if (chave.startsWith("on")) el.addEventListener(chave.slice(2), valor);
    else el.setAttribute(chave, valor === true ? "" : valor);
  }
  for (const filho of filhos.flat()) {
    if (filho == null || filho === false) continue;
    el.append(filho instanceof Node ? filho : document.createTextNode(String(filho)));
  }
  return el;
}

// ---------------------------------------------------------------- rede

class ErroApi extends Error {
  constructor(status, mensagem) {
    super(mensagem);
    this.status = status;
  }
}

async function api(caminho, opcoes = {}) {
  let resposta;
  try {
    resposta = await fetch(caminho, opcoes);
  } catch {
    throw new ErroApi(0, "Não foi possível falar com a API. Ela está rodando?");
  }
  let corpo = null;
  try { corpo = await resposta.json(); } catch { /* resposta sem corpo JSON */ }
  if (!resposta.ok) {
    // A API sempre devolve {detail: "..."} com a explicacao; 422 do FastAPI
    // devolve uma lista de erros de validacao.
    const d = corpo?.detail;
    const msg = typeof d === "string" ? d
      : Array.isArray(d) ? d.map((x) => x.msg).join("; ")
      : `erro ${resposta.status}`;
    throw new ErroApi(resposta.status, msg);
  }
  return corpo;
}

// ---------------------------------------------------------------- estado

const estado = {
  itens: [],
  cursor: null,
  total: 0,
  alertaAberto: null,
  caso: null,
  marcaSelecionada: null,
};

const CHAVE_ANALISTA = "mesa-triagem.analista";

// localStorage pode lancar (janela privada, dados do site bloqueados): o nome
// do analista e conveniencia, nunca motivo para a tela quebrar.
function lerAnalista() {
  try { return localStorage.getItem(CHAVE_ANALISTA) ?? ""; } catch { return ""; }
}
function gravarAnalista(nome) {
  try { localStorage.setItem(CHAVE_ANALISTA, nome); } catch { /* segue sem lembrar */ }
}

// ---------------------------------------------------------------- avisos

let timerAviso;
function avisar(mensagem, tipo = "ok") {
  const el = $("#aviso");
  el.textContent = mensagem;
  el.className = `aviso aviso-${tipo}`;
  el.hidden = false;
  clearTimeout(timerAviso);
  timerAviso = setTimeout(() => { el.hidden = true; }, 6000);
}

// ---------------------------------------------------------------- pecas

function classeNivel(nivel) {
  return nivel ? `nivel-${nivel.normalize("NFD").replace(/[\u0300-\u036f]/g, "")}` : "nivel-vazio";
}

function chipNivel(quem, nivel) {
  return h("span", { class: `chip ${classeNivel(nivel)}`, title: `nível de risco segundo ${quem}` },
    h("span", { class: "chip-quem" }, quem), nivel ?? "—");
}

const ROTULO_SITUACAO = {
  nao_triado: "não triado",
  concorda: "concorda",
  diverge: "diverge",
};

function chipSituacao(situacao) {
  return h("span", { class: `chip situacao situacao-${situacao}` }, ROTULO_SITUACAO[situacao]);
}

function marcaFundamentado(fundamentado) {
  if (fundamentado === true) return h("span", { class: "fund fund-ok" }, "✓ números conferem");
  if (fundamentado === false) return h("span", { class: "fund fund-erro" }, "✗ número sem procedência");
  return null;
}

function secao(titulo, ...conteudo) {
  return h("section", { class: "bloco" }, h("h3", {}, titulo), ...conteudo);
}

// ---------------------------------------------------------------- fila

async function carregarFila({ anexar = false } = {}) {
  const params = new URLSearchParams({ origem: $("#filtro-origem").value, limite: "50" });
  const filtroEstado = $("#filtro-estado").value;
  if (filtroEstado) params.set("estado", filtroEstado);
  // Paginacao pelo cursor que a propria API devolveu - nunca por numero de
  // pagina: a fila muda enquanto e lida (ver passo 2.1 do ROADMAP).
  if (anexar && estado.cursor) params.set("cursor", estado.cursor);

  const fila = await api(`/fila?${params}`);
  estado.itens = anexar ? [...estado.itens, ...fila.itens] : fila.itens;
  estado.cursor = fila.proximo_cursor;
  estado.total = fila.total;
  desenharFila();
}

function desenharFila() {
  $("#fila-total").textContent = `${estado.total} ${estado.total === 1 ? "caso" : "casos"}`;
  $("#fila-mais").hidden = !estado.cursor;

  if (!estado.itens.length) {
    $("#fila-lista").replaceChildren(h("li", { class: "fila-vazia" }, "Nenhum caso com estes filtros."));
    return;
  }
  $("#fila-lista").replaceChildren(...estado.itens.map(itemFila));
}

function itemFila(item) {
  const situacao = situacaoDaFila(item);
  const aberto = item.alerta_id === estado.alertaAberto;
  return h("li", {},
    h("a", {
      href: `#/alerta/${item.alerta_id}`,
      class: `item-fila situacao-borda-${situacao}${aberto ? " aberto" : ""}`,
      "aria-current": aberto ? "true" : null,
    },
      h("span", { class: "item-topo" },
        h("span", { class: "cliente" }, item.cliente_id),
        chipSituacao(situacao)),
      h("span", { class: "item-niveis" },
        chipNivel("regra", item.nivel_risco_regra),
        chipNivel("agente", item.nivel_risco_agente)),
      h("span", { class: "item-rodape" },
        h("span", {}, ROTULO_ESTADO[item.estado],
          item.analista_id ? ` · com ${item.analista_id}` : ""),
        marcaFundamentado(item.fundamentado))));
}

function mostrarErroFila(erro) {
  $("#fila-total").textContent = "";
  $("#fila-mais").hidden = true;
  $("#fila-lista").replaceChildren(h("li", { class: "erro" }, erro.message));
}

// ---------------------------------------------------------------- caso

async function abrirCaso(alertaId) {
  estado.alertaAberto = alertaId;
  estado.marcaSelecionada = null;
  desenharFila();

  const area = $("#caso");
  area.replaceChildren(h("p", { class: "carregando" }, "Carregando caso…"));
  try {
    const [caso, evidencias] = await Promise.all([
      api(`/alertas/${alertaId}`),
      api(`/alertas/${alertaId}/evidencias`),
    ]);
    // Cliques rapidos em dois casos: a resposta do primeiro pode chegar depois
    // da do segundo. So desenha se este ainda e o caso aberto.
    if (estado.alertaAberto !== alertaId) return;
    estado.caso = caso;
    area.replaceChildren(desenharCaso(caso, evidencias));
    area.scrollTop = 0;
  } catch (erro) {
    if (estado.alertaAberto !== alertaId) return;
    area.replaceChildren(h("div", { class: "erro" }, erro.message));
  }
}

function desenharCaso(caso, evidencias) {
  return h("article", { class: "caso-conteudo" },
    cabecalhoCaso(caso),
    h("div", { class: "caso-corpo" },
      colunaParecer(caso),
      colunaEvidencia(caso, evidencias)));
}

function cabecalhoCaso(caso) {
  const a = caso.alerta;
  const situacao = situacaoDaFila(a);
  const botoes = caso.transicoes_permitidas.map((destino) =>
    h("button", {
      type: "button",
      class: destino === "em_analise" ? "botao" : "botao botao-sec",
      onclick: () => transicionar(destino),
    }, ROTULO_TRANSICAO[destino] ?? destino));

  return h("header", { class: "caso-cab" },
    h("div", { class: "caso-titulo" },
      h("h1", {}, a.cliente_id),
      h("div", { class: "caso-chips" },
        chipNivel("regra", a.nivel_risco_regra),
        chipNivel("agente", a.nivel_risco_agente),
        chipSituacao(situacao),
        marcaFundamentado(a.fundamentado)),
      h("p", { class: "caso-sub" },
        `${a.qtd_operacoes} operações · volume ${formatarBRL(a.volume_total_brl)} · `,
        `${a.total_sinalizacoes} ${a.total_sinalizacoes === 1 ? "sinalização" : "sinalizações"}`,
        a.origem === "controle" ? " · cliente de controle (sem sinalização)" : "")),
    h("div", { class: "caso-acoes" },
      h("p", { class: "caso-estado" },
        h("span", { class: `estado estado-${a.estado}` }, ROTULO_ESTADO[a.estado]),
        a.analista_id ? h("span", { class: "dono" }, ` com ${a.analista_id}`) : null),
      botoes.length ? h("div", { class: "botoes" }, ...botoes) : null,
      // Nao ha botao de concluir: concluir exige a decisao registrada junto,
      // e isso e a Fase 4. Dizer isso e melhor que um botao desabilitado mudo.
      a.estado === "em_analise"
        ? h("p", { class: "nota-fase" }, "Concluir o caso exige registrar a decisão — próxima fase.")
        : null));
}

// ---------------------------------------------------------------- parecer

function colunaParecer(caso) {
  const p = caso.parecer;
  if (!p) {
    return h("div", { class: "coluna" },
      secao("Parecer do agente",
        h("p", { class: "vazio-bloco" },
          "Este caso ainda não foi triado. Quando o worker de triagem passar por ele, o parecer aparece aqui.")));
  }

  const marcas = caso.aderencia?.marcas ?? [];
  const problemas = marcas.filter((m) => m.classe === "atipico_incorreto" || m.classe === "nao_encontrado");
  const confirmados = marcas.filter((m) => m.classe === "confirmado").length;

  return h("div", { class: "coluna" },
    secao("Parecer do agente",
      h("p", { class: "tipologia" }, p.tipologia_suspeita ?? ""),
      origemDoParecer(p),
      p.erro_parsing ? h("div", { class: "alerta-caixa alerta-erro" },
        h("strong", {}, "O agente não produziu um parecer válido. "), p.erro_parsing) : null,
      p.justificativa ? justificativa(p.justificativa, marcas) : null,
      h("p", { id: "explicacao", class: "explicacao", hidden: true }),
      avisoDeAderencia(caso.aderencia, problemas, confirmados),
      p.red_flags.length
        ? h("div", { class: "red-flags" },
            h("h4", {}, "Sinais apontados pelo agente"),
            h("ul", {}, ...p.red_flags.map((f) => h("li", {}, f))))
        : null));
}

function origemDoParecer(p) {
  const partes = [`${p.modelo} · prompt ${p.versao_prompt}`];
  if (p.origem_registro === "importado_cache") {
    partes.push("registro importado do cache antigo (data inferida, não observada)");
  } else {
    partes.push(`gerado em ${formatarMomento(p.criado_em)}`);
  }
  if (p.reaproveitado_de) partes.push(`reaproveitado do parecer nº ${p.reaproveitado_de}, sem nova chamada ao modelo`);
  return h("p", { class: "meta" }, partes.join(" · "));
}

function justificativa(texto, marcas) {
  const trechos = segmentar(texto, marcas);
  return h("p", { class: "justificativa" },
    ...trechos.map(({ texto: pedaco, marca }) => {
      if (!marca) return pedaco;
      return h("button", {
        type: "button",
        class: `marca marca-${marca.classe}`,
        title: explicacaoDaMarca(marca),
        onclick: (evento) => selecionarMarca(marca, evento.currentTarget),
      }, pedaco);
    }));
}

function avisoDeAderencia(aderencia, problemas, confirmados) {
  if (!aderencia) return null;

  // Principio 2 do ROADMAP: o numero sem procedencia e MARCADO, com o motivo
  // escrito - o parecer nao e escondido.
  if (problemas.length) {
    return h("div", { class: "alerta-caixa alerta-erro" },
      h("strong", {}, problemas.length === 1 ? "Um número do parecer não tem procedência" : `${problemas.length} números do parecer não têm procedência`),
      h("ul", {}, ...problemas.map((m) =>
        h("li", {}, h("span", { class: "valor-citado" }, formatarBRL(m.valor)), " — ", explicacaoDaMarca(m)))));
  }
  if (!aderencia.fundamentado) {
    // Ex.: CLI-001 - tem sinalizacao, mas o parecer nao cita numero nenhum
    return h("div", { class: "alerta-caixa alerta-atencao" },
      h("strong", {}, "Parecer não fundamentado. "), aderencia.motivo);
  }
  if (confirmados) {
    return h("p", { class: "ok-linha" },
      `${confirmados === 1 ? "O valor citado confere" : `Os ${confirmados} valores citados conferem`} com a base do cliente. Clique em um número para ver de onde ele vem.`);
  }
  return h("p", { class: "ok-linha" }, aderencia.motivo);
}

function selecionarMarca(marca, botao) {
  const raiz = $("#caso");
  raiz.querySelectorAll(".alvo, .marca.selecionada").forEach((el) => el.classList.remove("alvo", "selecionada"));

  const explicacao = $("#explicacao");
  if (estado.marcaSelecionada === marca) {
    estado.marcaSelecionada = null;
    explicacao.hidden = true;
    return;
  }
  estado.marcaSelecionada = marca;
  botao.classList.add("selecionada");
  // Numero com problema ja tem a explicacao no aviso vermelho fixo logo abaixo
  // do texto - repeti-la aqui seria o mesmo paragrafo duas vezes seguidas.
  const temAvisoFixo = marca.classe === "atipico_incorreto" || marca.classe === "nao_encontrado";
  explicacao.textContent = explicacaoDaMarca(marca);
  explicacao.className = `explicacao explicacao-${marca.classe}`;
  explicacao.hidden = temAvisoFixo;

  const alvo = alvoDaFonte(marca.fonte);
  const alvos = [];
  if (alvo?.tipo === "agregado") {
    alvos.push(...raiz.querySelectorAll(`[data-agregado="${alvo.nome}"]`));
  } else if (alvo) {
    for (const linha of raiz.querySelectorAll("tr[data-op]")) {
      if (operacaoEhAlvo(JSON.parse(linha.dataset.op), alvo)) alvos.push(linha);
    }
    if (alvo.tipo === "dia") alvos.push(...raiz.querySelectorAll(`[data-dia="${alvo.data}"]`));
  }
  alvos.forEach((el) => el.classList.add("alvo"));

  const semMovimento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // "center", nao "nearest": com nearest a linha de destino parava colada na
  // borda de baixo da area visivel (visto no navegador, caso CLI-028)
  alvos[0]?.scrollIntoView({ block: "center", behavior: semMovimento ? "auto" : "smooth" });
}

// ---------------------------------------------------------------- evidencia

function colunaEvidencia(caso, evidencias) {
  return h("div", { class: "coluna" },
    blocoSinalizacoes(caso.sinalizacoes),
    blocoAgregados(caso),
    blocoOperacoes(caso.operacoes),
    blocoFerramentas(evidencias),
    blocoHistorico(caso.historico_parecer, caso.parecer));
}

function blocoSinalizacoes(sinalizacoes) {
  if (!sinalizacoes.length) {
    return secao("O que as regras apontaram",
      h("p", { class: "vazio-bloco" }, "Nenhuma regra disparou para este cliente."));
  }
  return secao("O que as regras apontaram",
    h("ul", { class: "sinalizacoes" }, ...sinalizacoes.map((s) => {
      const d = s.detalhe;
      if (s.regra === "fracionamento") {
        return h("li", { dataset: { dia: s.data } },
          h("span", { class: "regra-nome" }, "Fracionamento"),
          ` em ${formatarData(s.data)}: ${d.qtd_operacoes} operações somando ${formatarBRL(d.soma_do_dia)}, `,
          `a maior de ${formatarBRL(d.max_individual)}`);
      }
      return h("li", {},
        h("span", { class: "regra-nome" }, "Valor atípico"),
        ` ${s.operacao_id}: ${formatarBRL(d.valor_brl)}, acima do limite de ${formatarBRL(d.limite_atipico)} para este cliente`);
    })));
}

function blocoAgregados(caso) {
  // Nenhum agregado e CALCULADO aqui. O volume vem do alerta (gravado pelas
  // regras); media e mediana so aparecem quando o parecer as cita, com o valor
  // que o verificador conferiu.
  const citados = new Map();
  for (const m of caso.aderencia?.marcas ?? []) {
    const alvo = alvoDaFonte(m.fonte);
    if (alvo?.tipo === "agregado") citados.set(alvo.nome, m.valor);
  }
  const itens = [
    h("li", { dataset: { agregado: "volume" } },
      h("span", {}, "Volume total do cliente"),
      h("span", { class: "num" }, formatarBRL(caso.alerta.volume_total_brl))),
  ];
  for (const [nome, rotulo] of [["media", "Média do cliente"], ["mediana", "Mediana do cliente"]]) {
    if (citados.has(nome)) {
      itens.push(h("li", { dataset: { agregado: nome } },
        h("span", {}, rotulo, h("span", { class: "sub-rotulo" }, " (citada no parecer)")),
        h("span", { class: "num" }, formatarBRL(citados.get(nome)))));
    }
  }
  return secao("Agregados", h("ul", { class: "agregados" }, ...itens));
}

function blocoOperacoes(operacoes) {
  return secao(`Operações do cliente (${operacoes.length})`,
    h("div", { class: "tabela-rolagem" },
      h("table", { class: "operacoes" },
        h("thead", {}, h("tr", {},
          h("th", {}, "Data"), h("th", {}, "Operação"), h("th", { class: "num" }, "Valor (R$)"),
          h("th", {}, "Canal"), h("th", {}, "Tipo"), h("th", {}, "Contraparte"), h("th", {}, "Sinal"))),
        h("tbody", {}, ...operacoes.map((o) =>
          h("tr", {
            class: [o.flag_valor_atipico ? "op-atipica" : "", o.em_dia_de_fracionamento ? "op-frac" : ""].join(" ").trim() || null,
            dataset: { op: JSON.stringify({ id: o.id, data: o.data, canal: o.canal }) },
          },
            h("td", { class: "nowrap" }, formatarData(o.data)),
            h("td", { class: "mono" }, o.id),
            h("td", { class: "num" }, formatarBRL(o.valor_brl),
              o.moeda !== "BRL" ? h("span", { class: "moeda-orig" }, `${o.moeda} ${o.valor}`) : null),
            h("td", {}, o.canal),
            h("td", {}, o.tipo.replace(/_/g, " ")),
            h("td", { class: "contraparte" }, o.contraparte),
            h("td", { class: "sinais" },
              o.flag_valor_atipico ? h("span", { class: "chip chip-atipico", title: "operação sinalizada pela Regra 2" }, "atípica") : null,
              o.em_dia_de_fracionamento ? h("span", { class: "chip chip-frac", title: "dia que disparou a Regra 1" }, "dia fracionado") : null)))))));
}

function blocoFerramentas(evidencias) {
  if (!evidencias.length) {
    return secao("O que o agente consultou",
      h("p", { class: "vazio-bloco" }, "Nenhuma ferramenta registrada para este parecer."));
  }
  const semRetorno = evidencias.every((e) => e.payload === null);
  return secao("O que o agente consultou",
    semRetorno
      ? h("p", { class: "nota" },
          "O registro deste parecer guardou quais ferramentas o agente chamou, mas não o que elas devolveram — ele é anterior ao registro completo de evidências.")
      : null,
    h("ul", { class: "ferramentas" }, ...evidencias.map((e) =>
      h("li", {},
        h("code", {}, `${e.tool}(${Object.values(e.args).join(", ")})`),
        e.payload !== null
          ? h("details", {}, h("summary", {}, "ver o que a ferramenta devolveu"),
              h("pre", {}, JSON.stringify(e.payload, null, 2)))
          : null))));
}

const ROTULO_ORIGEM = {
  agente: "gerado nesta instalação",
  importado_cache: "importado do cache antigo",
};

function blocoHistorico(historico, atual) {
  if (!historico.length) {
    return secao("Histórico de pareceres", h("p", { class: "vazio-bloco" }, "Nenhum parecer ainda."));
  }
  // O append-only da Fase 1 ficando visivel: se este cliente ja recebeu outro
  // parecer, o analista ve - e ve se a data foi observada ou inferida.
  return secao(`Histórico de pareceres (${historico.length})`,
    h("ol", { class: "historico" }, ...historico.map((v) =>
      h("li", { class: v.parecer_id === atual?.parecer_id ? "atual" : null },
        h("span", { class: "hist-momento" }, formatarMomento(v.criado_em)),
        chipNivel("agente", v.nivel_risco),
        h("span", { class: "hist-origem" },
          ROTULO_ORIGEM[v.origem_registro] ?? v.origem_registro,
          // Uma copia reaproveitada tem data OBSERVADA (o momento do vinculo); o
          // registro importado original tem data INFERIDA do arquivo de cache.
          // Numa trilha de auditoria as duas nao podem parecer iguais.
          v.reaproveitado_de
            ? ` · cópia do nº ${v.reaproveitado_de}`
            : v.origem_registro === "importado_cache" ? " · data inferida" : ""),
        v.parecer_id === atual?.parecer_id ? h("span", { class: "chip chip-atual" }, "atual") : null))));
}

// ---------------------------------------------------------------- acoes

async function transicionar(destino) {
  const analista = $("#analista").value.trim();
  if (!analista) {
    avisar("Informe seu nome no campo Analista antes de pegar ou devolver um caso.", "erro");
    $("#analista").focus();
    return;
  }
  const alertaId = estado.alertaAberto;
  const cliente = estado.caso?.alerta.cliente_id ?? `#${alertaId}`;
  try {
    await api(`/alertas/${alertaId}/estado`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Analista": analista },
      body: JSON.stringify({ estado: destino }),
    });
    avisar(destino === "em_analise" ? `${cliente} agora está com você.` : `${cliente} voltou para a fila.`, "ok");
  } catch (erro) {
    // 409 ja vem com a explicacao da API (o que era permitido, ou que outro
    // analista pegou o caso antes) - mostrar como veio.
    avisar(erro.message, "erro");
  }
  await Promise.all([carregarFila().catch(mostrarErroFila), abrirCaso(alertaId)]);
}

// ---------------------------------------------------------------- inicio

function alertaDaRota() {
  const m = location.hash.match(/^#\/alerta\/(\d+)$/);
  return m ? Number(m[1]) : null;
}

async function iniciar() {
  const campo = $("#analista");
  campo.value = lerAnalista();
  campo.addEventListener("input", () => gravarAnalista(campo.value.trim()));

  $("#filtro-estado").addEventListener("change", () => carregarFila().catch(mostrarErroFila));
  $("#filtro-origem").addEventListener("change", () => carregarFila().catch(mostrarErroFila));
  $("#fila-mais").addEventListener("click", () => carregarFila({ anexar: true }).catch(mostrarErroFila));
  window.addEventListener("hashchange", () => {
    const id = alertaDaRota();
    if (id) abrirCaso(id);
  });

  try {
    await carregarFila();
  } catch (erro) {
    mostrarErroFila(erro);
  }
  const id = alertaDaRota();
  if (id) abrirCaso(id);
}

iniciar();
