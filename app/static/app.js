/* WasteStack — interface local (demonstração). Sem dependências além do Leaflet. */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const chave = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
const S = { token: null, eu: null, cat: null, aba: null, ponto: null, mapa: null, camadas: {}, chaveReporte: chave(), material: "" };
const COR = { baixo: "#c9d8c2", "médio": "#f0d27a", alto: "#e58f3c", "crítico": "#b93a2b" };
const ORDEM = ["recebido", "confirmado", "em_atendimento", "aguardando_confirmacao", "resolvido"];

async function api(caminho, opc = {}) {
  const r = await fetch(caminho, { method: opc.method || "GET", headers: { "Content-Type": "application/json", "X-Sessao": S.token || "" },
    body: opc.body ? JSON.stringify(opc.body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(typeof j.detail === "string" ? j.detail : "Não foi possível concluir a ação."); e.status = r.status; throw e; }
  return j;
}
function avisar(texto, erro = false) {
  const a = $("#aviso"); a.textContent = texto; a.className = "aviso" + (erro ? " erro" : ""); a.hidden = false;
  clearTimeout(avisar.t); avisar.t = setTimeout(() => (a.hidden = true), erro ? 7000 : 4500);
}
const tentar = fn => async (...a) => { try { return await fn(...a); } catch (e) { avisar(e.message, true); } };
const etqExemplo = x => (x ? '<span class="etq exemplo">EXEMPLO SIMULADO</span>' : "");
const provisorio = t => `<span class="etq prov">provisório</span> <small>${esc(t)}</small>`;

/* ---------------- entrada ---------------- */
async function telaEntrada() {
  $("#app").hidden = true; $("#quem").innerHTML = "";
  const p = await api("/api/perfis");
  const cid = p.perfis.filter(x => x.perfil === "cidadao"), ges = p.perfis.filter(x => x.perfil === "gestor");
  $("#entrada").hidden = false;
  $("#entrada").innerHTML = `<h1>Escolha um perfil para testar</h1>
    <p>Esta é uma <b>versão local de demonstração</b>. Os perfis são fictícios e os dados ficam só neste computador.</p>
    <h3>App Cidadão</h3><div class="cartoes">${cid.map(u => `<div class="cartao"><h3>${esc(u.apelido)}</h3><p class="nota">Reporta, confirma e acompanha.</p><button data-u="${esc(u.id)}">Entrar como ${esc(u.apelido)}</button></div>`).join("")}</div>
    <h3>Painel do Gestor</h3><div class="cartoes">${ges.map(u => `<div class="cartao"><h3>${esc(u.apelido)}</h3><label for="pin">PIN local</label><input id="pin" inputmode="numeric" maxlength="8" autocomplete="off"><p class="nota">O PIN aparece no terminal ao iniciar e em <code>data/operacional/credenciais_demo.json</code>.</p><button data-u="${esc(u.id)}" data-pin="1">Entrar como gestor</button></div>`).join("")}</div>
    <p class="nota">Para percorrer o ciclo completo, use dois cidadãos (um reporta, outro confirma) e o gestor. Abra uma janela anônima se quiser dois perfis ao mesmo tempo.</p>`;
  $("#entrada").onclick = tentar(async ev => {
    const b = ev.target.closest("button[data-u]"); if (!b) return;
    const r = await api("/api/sessao", { method: "POST", body: { usuario_id: b.dataset.u, pin: b.dataset.pin ? $("#pin").value : "" } });
    S.token = r.token; sessionStorage.setItem("ws_token", r.token); await iniciarApp();
  });
}

/* ---------------- estrutura ---------------- */
const ABAS = {
  cidadao: [["mapa", "Mapa"], ["reportar", "Reportar"], ["descarte", "Onde descartar"], ["area", "Minha área"], ["missoes", "Missões e ranking"], ["meus", "Meus reportes"]],
  gestor: [["fila", "Fila e rota"], ["risco", "Mapa de prioridade"], ["reportes", "Reportes e ações"], ["descarte", "Rede de descarte"], ["silencio", "Zonas de silêncio"], ["motor", "Parâmetros do motor"], ["contexto", "Linha de base e testes"], ["eventos", "Eventos"], ["b2b", "B2B (demonstração)"]],
};
async function iniciarApp() {
  S.eu = await api("/api/eu"); S.cat = S.cat || await api("/api/catalogo");
  $("#entrada").hidden = true; $("#app").hidden = false;
  $("#quem").innerHTML = `<span>${esc(S.eu.apelido)} · ${S.eu.perfil === "gestor" ? "gestor" : `<b id="pts">${S.eu.pontos}</b> pontos`}</span><button id="sair">Trocar perfil</button>`;
  $("#sair").onclick = () => { S.token = null; S.ponto = null; S.meus = null; S.material = ""; S.chaveReporte = chave(); limparMapa(); sessionStorage.removeItem("ws_token"); telaEntrada(); };
  $("#abas").innerHTML = ABAS[S.eu.perfil].map(([id, nome]) => `<button role="tab" data-aba="${id}">${nome}</button>`).join("");
  $("#abas").onclick = ev => { const b = ev.target.closest("button[data-aba]"); if (b) abrir(b.dataset.aba); };
  await iniciarMapa();
  abrir(ABAS[S.eu.perfil][0][0]);
}
async function atualizarPontos() { if (S.eu.perfil === "cidadao") { S.eu = await api("/api/eu"); const p = $("#pts"); if (p) p.textContent = S.eu.pontos; } }

/* ---------------- mapa ---------------- */
async function iniciarMapa() {
  if (!S.mapa) {
    S.mapa = L.map("mapa", { preferCanvas: true }).setView([-8.05, -34.92], 12);
    const base = await api("/api/mapa/base");
    S.camadas.vias = L.geoJSON(base.vias, { style: { color: "#b5beb6", weight: 1.4 }, onEachFeature: (f, l) => l.bindTooltip(f.properties.logradouro_norm) }).addTo(S.mapa);
    S.mapa.attributionControl.addAttribution("Mapa local · Prefeitura do Recife · sem imagens externas");
    S.camadas.bairros = L.geoJSON(base.bairros, { style: { color: "#44524c", weight: 1, fill: false }, interactive: false }).addTo(S.mapa);
    S.camadas.faixas = L.geoJSON(base.faixas, { style: { color: "#2b6cb0", weight: 0, fillColor: "#2b6cb0", fillOpacity: .28 }, interactive: false });
    for (const n of ["risco", "alertas", "descarte", "extra"]) S.camadas[n] = L.layerGroup().addTo(S.mapa);
    S.mapa.on("click", e => S.aoClicarMapa && S.aoClicarMapa(e.latlng));
  }
  setTimeout(() => S.mapa.invalidateSize(), 50);
}
function limparMapa() { for (const n of ["risco", "alertas", "descarte", "extra"]) S.camadas[n].clearLayers(); S.mapa.removeLayer(S.camadas.faixas); S.aoClicarMapa = null; legenda(""); }
function legenda(html) { const l = $("#legenda"); l.innerHTML = html; l.hidden = !html; }
const LEG_RISCO = `<b>Nível de prioridade estimado</b><br>${Object.entries(COR).map(([k, c]) => `<i style="background:${c}"></i>${k}`).join(" &nbsp;")}<br><small>Pontuação heurística provisória. Não é previsão comprovada nem contagem de chamados.</small>`;
async function desenharRisco() {
  const r = await api("/api/mapa/risco");
  for (const h of r.hexagonos) {
    const p = L.polygon(h.contorno, { color: COR[h.nivel], weight: h.na_fila ? 1.5 : 0, fillColor: COR[h.nivel], fillOpacity: h.nivel === "baixo" ? .25 : .55 });
    if (h.score !== undefined) p.bindPopup(`<b>${esc(h.bairro || "")}</b> · ${esc(h.regime)}<br>Score ${h.score} (${esc(h.nivel)})${h.zona_silencio ? "<br>Zona de silêncio (hipótese)" : ""}<ul class="motivos">${h.motivos.map(m => `<li>${esc(m)}</li>`).join("")}</ul>`);
    p.addTo(S.camadas.risco);
  }
  return r;
}
async function desenharAlertas(aoAbrir) {
  const r = await api("/api/alertas");
  for (const a of r.alertas) {
    const m = L.circleMarker([a.lat_aprox, a.lon_aprox], { radius: 8, color: a.exemplo_simulado ? "#7a4a9e" : "#14231f", weight: 2, fillColor: a.estado === "aguardando_confirmacao" ? "#157a62" : "#fff", fillOpacity: .95 });
    m.bindTooltip(`${a.categoria}${a.exemplo_simulado ? " (exemplo)" : ""}`); m.on("click", ev => { L.DomEvent.stopPropagation(ev); aoAbrir(a); }); m.addTo(S.camadas.alertas);
  }
  return r;
}
function marcarPonto(ll) { S.ponto = ll; S.camadas.extra.clearLayers(); L.marker(ll).addTo(S.camadas.extra); }

/* ---------------- abas ---------------- */
function abrir(aba) {
  // As telas carregam em fila: uma aba antiga que ainda estava carregando não sobrescreve a aba escolhida depois.
  S.aba = aba;
  for (const b of $("#abas").children) b.setAttribute("aria-selected", b.dataset.aba === aba);
  $("#painel").innerHTML = "<p>Carregando…</p>";
  S.render = (S.render || Promise.resolve()).then(async () => {
    if (S.aba !== aba) return;
    limparMapa(); $("#painel").onclick = null; $("#painel").innerHTML = "<p>Carregando…</p>"; $("#painel").scrollTop = 0;
    await tentar(TELAS[aba])();
  });
  return S.render;
}
const P = html => ($("#painel").innerHTML = html);
const passos = est => `<div class="passos">${ORDEM.map(e => `<span class="${e === est ? "atual" : ""}">${esc(S.cat.estados[e])}</span>`).join("")}${est === "reaberto" ? '<span class="atual">Reaberto</span>' : ""}</div>`;
const contagem = c => Object.entries(c || {}).map(([k, n]) => `${n}× “${esc(S.cat.respostas[k])}”`).join(" · ") || "sem confirmações";

function cartaoAlerta(a) {
  const meu = S.meus && S.meus.has(a.id);
  const botoes = a.estado === "em_atendimento" ? '<p class="nota">Em atendimento: a confirmação abre quando o gestor registrar a conclusão.</p>'
    : `<div class="linha">${Object.entries(S.cat.respostas).filter(([k]) => a.estado !== "aguardando_confirmacao" || k !== "nao_encontrei").map(([k, t]) => `<button class="sec" data-conf="${a.id}" data-r="${k}">${esc(t[0].toUpperCase() + t.slice(1))}</button>`).join("")}</div>`;
  return `<div class="item"><h3>${esc(a.categoria)} ${etqExemplo(a.exemplo_simulado)}</h3>
    <p>${esc(a.bairro || "")} · ${esc(a.volume)} · atualizado ${esc(a.visto)}${a.beira_de_rio ? ' · <span class="etq">beira de rio</span>' : ""}</p>${passos(a.estado)}
    <p class="nota">${contagem(a.confirmacoes)} · posição ${esc(a.posicao)}</p>
    ${a.estado === "aguardando_confirmacao" ? '<div class="caixa-nota">O gestor registrou a limpeza. <b>Está limpo mesmo?</b></div>' : ""}
    ${meu && a.estado !== "aguardando_confirmacao" ? '<p class="nota">Este reporte é seu: outra pessoa precisa confirmar.</p>' : botoes}</div>`;
}
async function responder(id, resposta) {
  const r = await api(`/api/reportes/${id}/confirmacoes`, { method: "POST", body: { resposta, chave: chave() } });
  avisar(`Registrado: ${r.estado_rotulo}. +${r.pontos_ganhos} pontos${r.missoes_concluidas.length ? " · missão concluída: " + r.missoes_concluidas.map(m => m.titulo).join(", ") : ""}`);
  await atualizarPontos(); abrir(S.aba);
}
document.addEventListener("click", tentar(async ev => { const b = ev.target.closest("button[data-conf]"); if (b) await responder(b.dataset.conf, b.dataset.r); }));

const TELAS = {
  /* ===== cidadão ===== */
  async mapa() {
    const meus = await api("/api/meus-reportes"); S.meus = new Set(meus.reportes.map(r => r.id));
    await desenharRisco(); S.mapa.addLayer(S.camadas.faixas);
    legenda(LEG_RISCO + '<br><i style="background:#2b6cb0;opacity:.5"></i>faixa de proteção de rio &nbsp; ◯ alerta &nbsp; <span style="color:#7a4a9e">◯</span> exemplo');
    const r = await desenharAlertas(a => { $("#detalhe").innerHTML = cartaoAlerta(a); });
    P(`<h2>Mapa ao vivo</h2><p>${r.alertas.length} alertas ativos. Toque num círculo para ver e responder <b>“ainda está aí?”</b>.</p>
      <div class="caixa-nota">${esc(r.privacidade)}</div><div id="detalhe"></div>
      <h3>Alertas ativos</h3>${r.alertas.map(cartaoAlerta).join("") || "<p>Nenhum alerta ativo.</p>"}`);
  },
  async reportar() {
    await desenharAlertas(() => {});
    legenda("Clique no mapa para marcar <b>onde está o acúmulo</b>.");
    S.aoClicarMapa = ll => { marcarPonto(ll); $("#onde").textContent = `Local marcado: ${ll.lat.toFixed(5)}, ${ll.lng.toFixed(5)}`; $("#enviar").disabled = false; };
    P(`<h2>Reportar acúmulo</h2><p>${provisorio(S.cat.provisorio)}</p>
      <p id="onde" class="caixa-nota alerta">1. Clique no mapa para marcar o local da ocorrência.</p>
      <label for="cat">2. O que é?</label><select id="cat">${S.cat.categorias.map(c => `<option>${esc(c)}</option>`).join("")}</select>
      <label for="vol">3. Quanto?</label><select id="vol">${Object.entries(S.cat.volumes).map(([k, t]) => `<option value="${k}">${esc(t)}</option>`).join("")}</select>
      <label for="txt">Observação (opcional)</label><textarea id="txt" rows="2" maxlength="280" placeholder="Não escreva nomes, telefones ou endereços de pessoas."></textarea>
      <div class="caixa-nota">📷 ${esc(S.cat.foto)}</div>
      <div class="linha"><button id="enviar" disabled>Enviar reporte</button></div>
      <p class="nota">O local marcado é o da ocorrência. A sua localização pessoal não é coletada. Os outros veem o alerta no centro de um hexágono de ~200 m, sem autor.</p><div id="resultado"></div>`);
    if (S.ponto) S.aoClicarMapa(S.ponto);
    $("#enviar").onclick = tentar(async () => {
      const r = await api("/api/reportes", { method: "POST", body: { lat: S.ponto.lat, lon: S.ponto.lng, categoria: $("#cat").value, volume: $("#vol").value, texto: $("#txt").value, chave: S.chaveReporte } });
      S.chaveReporte = chave();
      if (r.duplicado) { $("#resultado").innerHTML = `<div class="caixa-nota alerta"><b>${esc(r.mensagem)}</b></div>${cartaoAlerta(r.alerta_existente)}`; return; }
      const c = r.contexto, pr = r.prioridade;
      $("#resultado").innerHTML = `<div class="item"><h3>Reporte recebido · +${r.pontos_ganhos} pontos${r.bonus ? ` (em dobro: ${esc(r.bonus)})` : ""}</h3>${passos("recebido")}
        ${r.missoes_concluidas.map(m => `<p>🏁 Missão concluída: <b>${esc(m.titulo)}</b> (+${m.pontos})</p>`).join("")}
        <p><b>Prioridade da área agora:</b> <span class="etq ${esc(pr.faixa)}">${esc(pr.faixa)}</span> ${pr.na_fila ? "· entrou na fila do gestor" : "· abaixo do limiar da fila"}</p>
        <p class="nota">A prioridade vem de uma pontuação heurística provisória; sobe com confirmações de outras pessoas.</p>
        <h3>Contexto do lugar</h3>${htmlContexto(c)}</div>`;
      await atualizarPontos(); S.camadas.alertas.clearLayers(); await desenharAlertas(() => {});
    });
  },
  async descarte() {
    const r = await api("/api/descarte" + (S.material ? "?material=" + S.material : ""));
    for (const p of r.locais) if (p.lat) L.circleMarker([p.lat, p.lon], { radius: 6, color: "#0f5c4a", fillColor: p.tipo === "ecoestação" ? "#0f5c4a" : "#fff", fillOpacity: 1, weight: 2 }).bindPopup(`<b>${esc(p.nome)}</b><br>${esc(p.tipo)} · ${esc(p.materiais)}<br><small>${esc(p.situacao)}</small>`).addTo(S.camadas.descarte);
    legenda("● ecoestação &nbsp; ○ outros locais<br><small>Listas públicas históricas; situação a confirmar.</small>");
    P(`<h2>Onde descartar</h2><div class="caixa-nota alerta">${esc(r.aviso)}</div>
      <div class="linha"><button class="chip" data-mat="" aria-pressed="${!S.material}">todos</button>${r.materiais.map(m => `<button class="chip" data-mat="${m}" aria-pressed="${S.material === m}">${m}</button>`).join("")}</div>
      <p class="nota">${r.locais.length} locais${S.material ? " que recebem " + esc(S.material) : ""}.</p>
      ${r.locais.map(p => `<div class="item"><h3>${esc(p.nome)} <span class="etq">${esc(p.tipo)}</span>${p.localizacao === "pendente" ? '<span class="etq prov">localização pendente</span>' : ""}</h3>
        <p>${esc(p.bairro || "bairro não informado")}${p.endereco_na_fonte ? " · " + esc(p.endereco_na_fonte) : ""}</p><p><b>Recebe:</b> ${esc(p.materiais || "não informado")}</p>
        <p><b>Situação:</b> ${esc(p.situacao)} · <b>verificado em:</b> ${esc(p.data_verificacao)}${p.relatos_do_app ? ` · ${p.relatos_do_app} relato(s) do app` : ""}</p>
        ${p.data_abertura ? `<p class="nota">Abertura (fonte secundária): ${esc(p.data_abertura)}</p>` : ""}${p.divergencia_registrada ? `<p class="nota">Divergência entre fontes: ${esc(p.divergencia_registrada)}</p>` : ""}
        <p class="nota">Horário: ${esc(p.horario)}. ${esc(p.regra_uso)}</p><p class="nota">Materiais: ${esc(p.origem_material)}. Fonte: ${esc(p.fontes)}</p>
        ${S.eu.perfil === "cidadao" ? `<div class="linha">${Object.entries(S.cat.respostas_descarte).map(([k, t]) => `<button class="sec" data-pd="${p.ponto_id}" data-r="${k}">${esc(t)}</button>`).join("")}</div>` : ""}</div>`).join("")}`);
    $("#painel").onclick = tentar(async ev => {
      const m = ev.target.closest("button[data-mat]"); if (m) { S.material = m.dataset.mat; return abrir("descarte"); }
      const b = ev.target.closest("button[data-pd]"); if (!b) return;
      const x = await api(`/api/descarte/${b.dataset.pd}/confirmacoes`, { method: "POST", body: { resposta: b.dataset.r, material: S.material, chave: chave() } });
      avisar(`Obrigado! +${x.pontos_ganhos} pontos${x.missoes_concluidas.length ? " · missão concluída" : x.pontos_ganhos ? "" : " (este ponto já pontuou neste mês)"}`); await atualizarPontos(); abrir("descarte");
    });
  },
  async area() {
    legenda("Clique no mapa para ver o contexto de um lugar.");
    P(`<h2>Minha área</h2><p>Clique num ponto do mapa. Nada é guardado: a consulta serve só para mostrar o contexto.</p><div id="ctx"></div>`);
    S.aoClicarMapa = tentar(async ll => { marcarPonto(ll); const c = await api(`/api/contexto?lat=${ll.lat}&lon=${ll.lng}`); $("#ctx").innerHTML = `<div class="item">${htmlContexto(c)}</div>`; });
    if (S.ponto) S.aoClicarMapa(S.ponto);
  },
  async missoes() {
    const [m, r] = await Promise.all([api("/api/missoes"), api("/api/ranking")]);
    for (const x of m.missoes) if (x.lat) L.circleMarker([x.lat, x.lon], { radius: 9, color: "#234b7a", fillColor: x.concluida ? "#234b7a" : "#fff", fillOpacity: .9 }).bindTooltip(x.titulo + (x.concluida ? " (concluída)" : "")).addTo(S.camadas.extra);
    legenda("◯ área de missão (zona de silêncio: hipótese de pouco registro)");
    const gerais = m.missoes.filter(x => !x.h3), areas = m.missoes.filter(x => x.h3);
    P(`<h2>Missões e ranking</h2><p>Você tem <b>${m.pontos}</b> pontos. ${provisorio("Pontos simbólicos e regras de teste.")}</p>
      <h3>Missões</h3>${gerais.map(x => `<div class="item"><h3>${x.concluida ? "✅ " : ""}${esc(x.titulo)} <span class="etq">+${x.pontos}</span></h3><p>${esc(x.descricao)}</p></div>`).join("")}
      <div class="item"><h3>Ajude a mapear áreas com pouco registro <span class="etq">+20 cada</span></h3><p>${areas.length} áreas no mapa (${areas.filter(x => x.concluida).length} concluídas por você). Reporte ou confirme um alerta dentro de uma delas.</p>
      <p class="nota">Bairros: ${[...new Set(areas.map(x => x.bairro_nome))].map(esc).join(", ")}. São áreas onde há pouco registro para a carência do lugar; não são “áreas sujas”.</p></div>
      <h3>Ranking de participação</h3><div class="caixa-nota">${esc(r.explicacao)}</div>
      <table><tr><th>Participante</th><th class="n">Pontos</th></tr>${r.participantes.map(p => `<tr><td>${esc(p.apelido)} ${etqExemplo(p.exemplo_simulado)}</td><td class="n">${p.pontos}</td></tr>`).join("")}</table>
      <table><tr><th>Bairro</th><th class="n">Participações (uso local)</th></tr>${r.bairros_por_participacao.map(b => `<tr><td>${esc(b.bairro)}</td><td class="n">${b.participacoes}</td></tr>`).join("") || '<tr><td colspan="2">Ainda sem participações no uso local.</td></tr>'}</table>
      <h3>Regras</h3><ul class="motivos">${m.regras.map(x => `<li>${esc(x)}</li>`).join("")}</ul>`);
  },
  async meus() {
    const r = await api("/api/meus-reportes");
    for (const x of r.reportes) L.marker([x.lat, x.lon]).bindTooltip(x.categoria).addTo(S.camadas.extra);
    P(`<h2>Meus reportes</h2><div class="caixa-nota">${esc(r.privacidade)}</div>
      ${r.reportes.map(x => `<div class="item"><h3>${esc(x.categoria)} · ${esc(x.bairro || "")}</h3>${passos(x.estado)}
        <p class="nota">${esc(x.volume)} · criado em ${esc(x.criado_em.slice(0, 16).replace("T", " "))} UTC · ${contagem(x.confirmacoes)}</p>
        <p>Prioridade da área: <span class="etq ${esc(x.prioridade_da_area.faixa)}">${esc(x.prioridade_da_area.faixa)}</span> ${x.prioridade_da_area.na_fila ? "· na fila do gestor" : ""}</p>
        ${x.pode_confirmar_resultado ? `<div class="caixa-nota"><b>O gestor registrou a limpeza. Confirme o resultado:</b><div class="linha"><button data-conf="${x.id}" data-r="limpo">Já foi limpo</button><button class="sec" data-conf="${x.id}" data-r="ainda">Ainda está aqui</button></div></div>` : ""}
        ${x.pode_reabrir ? `<p><button class="sec" data-conf="${x.id}" data-r="ainda">O problema voltou — reabrir</button><small> Disponível por 30 dias após a resolução.</small></p>` : ""}
        <table><tr><th>Quando (UTC)</th><th>O que aconteceu</th></tr>${x.historico.transicoes.map(t => `<tr><td>${esc(t.em.slice(0, 16).replace("T", " "))}</td><td>${t.de ? esc(t.de) + " → " : ""}<b>${esc(t.para)}</b><br><small>${esc(t.motivo)}</small></td></tr>`).join("")}
        ${x.historico.acoes_do_gestor.map(a => `<tr><td>${esc(a.em.slice(0, 16).replace("T", " "))}</td><td>Gestor: ${esc(a.acao)} (${esc(a.orgao)})${a.nota ? "<br><small>" + esc(a.nota) + "</small>" : ""}</td></tr>`).join("")}</table></div>`).join("") || "<p>Você ainda não reportou nada. Use a aba Reportar.</p>"}
      <h3>Como os estados mudam</h3><p>${provisorio("regras de teste")}</p><ul class="motivos">${S.cat.transicoes.map(t => `<li>${esc(t)}</li>`).join("")}</ul>`);
  },

  /* ===== gestor ===== */
  async fila() {
    const f = await api("/api/gestor/fila"); S.regimeRota = S.regimeRota || "morro";
    const rota = await api("/api/gestor/rota?regime=" + S.regimeRota + (S.setorRota ? "&setor=" + encodeURIComponent(S.setorRota) : ""));
    await desenharRisco(); legenda(LEG_RISCO);
    if (rota.paradas.length) { L.polyline(rota.paradas.map(p => [p.lat, p.lon]), { color: "#14231f", weight: 2, dashArray: "6 5" }).addTo(S.camadas.extra);
      rota.paradas.forEach(p => L.marker([p.lat, p.lon], { icon: L.divIcon({ className: "", html: `<div style="background:#14231f;color:#fff;border-radius:99px;width:22px;height:22px;text-align:center;font:600 12px/22px sans-serif">${p.ordem}</div>` }) }).addTo(S.camadas.extra)); }
    const lista = (nome, itens) => `<h3>${nome} (${itens.length})</h3>${itens.map(i => `<div class="item"><h3>${i.posicao}. ${esc(i.bairro || "sem bairro")} <span class="etq ${esc(i.faixa)}">score ${i.score}</span>${i.zona_silencio ? '<span class="etq">zona de silêncio</span>' : ""}</h3>
      <ul class="motivos">${i.motivos.map(m => `<li>${esc(m)}</li>`).join("")}</ul>${i.reportes.length ? `<p><b>${i.reportes.length} reporte(s) do app nesta área:</b> ${i.reportes.map(r => `${esc(r.categoria)} (${esc(r.estado_rotulo)}) ${etqExemplo(r.exemplo_simulado)}`).join("; ")}</p>` : '<p class="nota">Sem reporte do app: entra pelo histórico e pelo contexto.</p>'}</div>`).join("") || "<p class='nota'>Nenhum item acima do limiar.</p>"}`;
    P(`<h2>Fila priorizada e sequência sugerida</h2><p>Cenário <b>${esc(f.cenario)}</b> · limiar ${f.limiar} · capacidade ${f.capacidade_dia} por lista · ${f.total_acima_do_limiar} hexágonos acima do limiar. ${provisorio("pesos e limiar de teste")}</p>
      <div class="caixa-nota alerta">${esc(f.aviso)}</div>
      <h3>Sequência sugerida</h3><div class="linha">${["morro", "planicie", "indefinido"].map(r => `<button class="chip" data-reg="${r}" aria-pressed="${S.regimeRota === r}">${r === "planicie" ? "planície" : r}</button>`).join("")}</div>
      <label for="setor-rota">Setor de coleta da sequência</label><select id="setor-rota">${rota.setores.map(s => `<option ${s === rota.setor ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
      <p>${rota.paradas.length} paradas · ${rota.total_linha_reta_km} km <b>em linha reta</b>.</p><p class="nota">${esc(rota.metodo)}. ${esc(rota.aviso)}</p>
      ${lista("Morro", f.filas.morro)}${lista("Planície", f.filas.planicie)}${lista("Regime indefinido (sem base para classificar)", f.filas.indefinido)}
      <h3>Reportes do app fora da fila (${f.reportes_fora_da_fila.length})</h3><p class="nota">Áreas abaixo do limiar. Continuam visíveis em “Reportes e ações”.</p>`);
    $("#painel").onclick = ev => { const b = ev.target.closest("button[data-reg]"); if (b) { S.regimeRota = b.dataset.reg; S.setorRota = null; abrir("fila"); } };
    $("#setor-rota").onchange = ev => { S.setorRota = ev.target.value; abrir("fila"); };
  },
  async risco() {
    const r = await desenharRisco(); legenda(LEG_RISCO); const h = r.hexagonos, n = k => h.filter(x => x.nivel === k).length;
    P(`<h2>Mapa de prioridade</h2><p>Clique num hexágono para ver score, regime e motivos. Cenário <b>${esc(r.cenario)}</b>.</p><div class="caixa-nota alerta">${esc(r.explicacao)}</div>
      <table><tr><th>Nível</th><th class="n">Hexágonos</th></tr>${Object.keys(COR).map(k => `<tr><td><span class="etq ${k}">${k}</span></td><td class="n">${n(k)}</td></tr>`).join("")}</table>
      <table><tr><th>Regime</th><th class="n">Hexágonos</th><th class="n">Na fila</th></tr>${["morro", "planicie", "indefinido"].map(g => `<tr><td>${g}</td><td class="n">${h.filter(x => x.regime === g).length}</td><td class="n">${h.filter(x => x.regime === g && x.na_fila).length}</td></tr>`).join("")}</table>
      <p class="nota">${esc(r.fontes)} “Indefinido” = sem evidência suficiente para classificar morro ou planície; usa pesos neutros.</p>`);
  },
  async reportes() {
    const r = await api("/api/gestor/reportes");
    for (const x of r.reportes) L.circleMarker([x.lat, x.lon], { radius: 7, color: x.exemplo_simulado ? "#7a4a9e" : "#14231f", fillColor: "#fff", fillOpacity: 1 }).bindTooltip(`${x.categoria} · ${x.estado_rotulo}`).addTo(S.camadas.alertas);
    const acoes = x => ({ recebido: ["vistoria", "iniciar_atendimento"], confirmado: ["vistoria", "iniciar_atendimento"], reaberto: ["vistoria", "iniciar_atendimento"], em_atendimento: ["vistoria", "concluir_limpeza"] }[x.estado] || []);
    P(`<h2>Reportes e ações</h2><p>O gestor vê o ponto exato da ocorrência, nunca quem reportou. Ao registrar “limpeza concluída”, o cidadão é chamado a confirmar.</p>
      ${r.reportes.map(x => `<div class="item"><h3>${esc(x.categoria)} · ${esc(x.bairro || "")} ${etqExemplo(x.exemplo_simulado)}</h3>${passos(x.estado)}
        <p>${esc(x.volume)} · área com score ${x.score_da_area} <span class="etq ${esc(x.faixa)}">${esc(x.faixa)}</span> · ${esc(x.regime)} · ${contagem(x.confirmacoes)}</p>
        <ul class="motivos">${x.motivos.map(m => `<li>${esc(m)}</li>`).join("")}</ul>
        ${acoes(x).length ? `<div class="linha"><select data-org="${x.id}" aria-label="Órgão"><option>Emlurb</option><option>Defesa Civil</option><option>Conviva</option><option>Outro</option></select></div><div class="linha">${acoes(x).map(a => `<button ${a === "vistoria" ? 'class="sec"' : ""} data-acao="${a}" data-id="${x.id}">${esc(S.cat.acoes_gestor[a])}</button>`).join("")}</div>` : `<p class="nota">${x.estado === "aguardando_confirmacao" ? "Aguardando o cidadão confirmar o resultado." : "Sem ação pendente."}</p>`}
        ${x.historico.acoes_do_gestor.length ? `<p class="nota">Ações: ${x.historico.acoes_do_gestor.map(a => `${esc(a.acao)} (${esc(a.orgao)}, ${esc(a.em.slice(0, 16).replace("T", " "))})`).join("; ")}</p>` : ""}</div>`).join("")}`);
    $("#painel").onclick = tentar(async ev => {
      const b = ev.target.closest("button[data-acao]"); if (!b) return;
      const x = await api("/api/gestor/acoes", { method: "POST", body: { reporte_id: b.dataset.id, acao: b.dataset.acao, orgao: $(`select[data-org="${b.dataset.id}"]`).value, chave: chave() } });
      avisar(`Registrado: ${x.estado_rotulo}. ${x.proximo_passo || ""}`); abrir("reportes");
    });
  },
  async silencio() {
    const r = await api("/api/gestor/silencio");
    for (const z of r.zonas) if (z.contorno) L.polygon(z.contorno, { color: "#234b7a", weight: 1, fillColor: z.zona_silencio ? "#234b7a" : "#999", fillOpacity: .5 }).bindPopup(`${esc(z.bairro_nome)} · ${esc(z.regime)}<br>carência (posto): ${(100 * z.esperado_rank).toFixed(0)}% · registro (posto): ${(100 * z.registrado_rank).toFixed(0)}%<br>reportes do app: ${z.reportes_do_app}`).addTo(S.camadas.extra);
    legenda('<i style="background:#234b7a"></i>zona de silêncio &nbsp; <i style="background:#999"></i>inconclusivo');
    const zs = r.zonas.filter(z => z.zona_silencio), porB = {}; zs.forEach(z => (porB[z.bairro_nome] = (porB[z.bairro_nome] || 0) + 1));
    P(`<h2>Zonas de silêncio</h2><div class="caixa-nota alerta"><b>${esc(r.interpretacao)}</b></div><p>${zs.length} hexágonos marcados; ${r.zonas.length - zs.length} inconclusivos (bairro com pouca demanda localizada).</p>
      <p class="nota"><b>Método:</b> ${esc(r.metodo)}. <b>Fontes:</b> ${esc(r.fonte)}.</p>
      <table><tr><th>Bairro</th><th class="n">Hexágonos</th></tr>${Object.entries(porB).sort((a, b) => b[1] - a[1]).map(([b, n]) => `<tr><td>${esc(b)}</td><td class="n">${n}</td></tr>`).join("")}</table>
      <p class="nota">A lista por bairro é de uso do gestor e não vira ranking público. No app, essas áreas viram missões com bônus.</p>`);
  },
  async motor() {
    const r = await api("/api/gestor/motor"); const cfg = r.config, comps = Object.keys(r.componentes);
    const bloco = nome => `<h3>Cenário “${esc(nome)}”</h3><p class="nota">${esc(cfg.cenarios[nome].descricao || "")}</p><div class="pesos"><b>Componente</b><b>Morro</b><b>Planície</b><b>Indefinido</b>
      ${comps.map(c => `<span>${esc(r.componentes[c])}</span>${["morro", "planicie", "indefinido"].map(g => `<input type="number" step="0.05" min="0" max="1" data-c="${nome}|${g}|${c}" value="${cfg.cenarios[nome][g][c]}" aria-label="${c} ${g}">`).join("")}`).join("")}</div>`;
    P(`<h2>Parâmetros do motor</h2><div class="caixa-nota alerta">${esc(r.aviso)} Score = 100 × soma(peso × componente), × fator nas zonas de silêncio. Os pesos de cada coluna são normalizados para somar 1.</div>
      <label for="cen">Cenário ativo</label><select id="cen">${Object.keys(cfg.cenarios).map(c => `<option ${c === cfg.cenario_ativo ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <div class="linha"><div><label for="lim">Limiar da fila (0–100)</label><input id="lim" type="number" min="0" max="100" value="${cfg.limiar_fila}"></div>
      <div><label for="cap">Capacidade por lista/dia</label><input id="cap" type="number" min="1" max="500" value="${cfg.capacidade_dia}"></div>
      <div><label for="fat">Fator zona de silêncio</label><input id="fat" type="number" step="0.05" min="1" max="2" value="${cfg.fator_zona_silencio}"></div></div>
      ${Object.keys(cfg.cenarios).map(bloco).join("")}
      <div class="linha"><button id="salvar">Salvar e recalcular</button><button class="sec" id="padrao">Voltar ao padrão do arquivo</button></div><p id="efeito" class="nota"></p>`);
    $("#salvar").onclick = tentar(async () => {
      cfg.cenario_ativo = $("#cen").value; cfg.limiar_fila = +$("#lim").value; cfg.capacidade_dia = +$("#cap").value; cfg.fator_zona_silencio = +$("#fat").value;
      document.querySelectorAll("input[data-c]").forEach(i => { const [n, g, c] = i.dataset.c.split("|"); cfg.cenarios[n][g][c] = +i.value; });
      const x = await api("/api/gestor/motor", { method: "PUT", body: cfg }); avisar("Parâmetros salvos."); $("#efeito").textContent = `Com estes parâmetros, ${x.acima_do_limiar} hexágonos ficam acima do limiar. Veja em “Fila e rota”.`;
    });
    $("#padrao").onclick = tentar(async () => { await api("/api/gestor/motor", { method: "DELETE" }); avisar("Padrão restaurado."); abrir("motor"); });
  },
  async contexto() {
    const r = await api("/api/gestor/contexto"); const mi = v => "R$ " + (v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + " mi";
    const nf = (v, d = 1) => (v == null ? "—" : Number(v).toLocaleString("pt-BR", { maximumFractionDigits: d }));
    P(`<h2>Linha de base e testes</h2>
      <h3>Contexto orçamentário</h3><table><tr><th>Ano</th><th>Ação</th><th class="n">Previsto</th><th class="n">% da LOA</th><th class="n">1% vale</th></tr>${r.orcamento.map(o => `<tr><td>${o.ano}</td><td>${esc(o.zeladoria)}</td><td class="n">${mi(o.valor)}</td><td class="n">${nf(o.pct_loa)}</td><td class="n">${mi(o.valor / 100)}</td></tr>`).join("")}</table><p class="nota">${esc(r.orcamento_nota)}</p>
      <h3>Como é hoje: Central 156</h3><table><tr><th>Categoria</th><th class="n">Chamados</th><th class="n">% atendido em 30 dias</th></tr>${r.base156.map(o => `<tr><td>${esc(o.categoria)}</td><td class="n">${nf(o.chamados, 0)}</td><td class="n">${nf(o.pct_atendido_30d)}</td></tr>`).join("")}</table>
      <table><tr><th>Fila aberta</th><th>Movimento</th><th>Idade</th><th class="n">Pedidos</th></tr>${r.fila156.map(o => `<tr><td>${esc(o.categoria)}</td><td>${esc(o.movimento)}</td><td>${esc(o.faixa_idade)}</td><td class="n">${nf(o.pedidos, 0)}</td></tr>`).join("")}</table><p class="nota">${esc(r.base156_nota)}</p>
      <h3>Teste de partida do score</h3><table><tr><th>Critério</th><th>Unidade</th><th class="n">km de via no topo</th><th class="n">% dos chamados de 2026</th></tr>${r.teste_partida.map(o => `<tr><td>${esc(o.criterio)}</td><td>${esc(o.unidade)}</td><td class="n">${nf(o.km_de_via_no_topo)}</td><td class="n">${nf(o.pct_chamados_2026_capturados)}</td></tr>`).join("")}</table>
      <div class="caixa-nota alerta">${esc(r.teste_nota)} Chamados medem registro de demanda. O quadro acima mistura unidades e é apenas descritivo. O score também considera risco estrutural; a hipótese de subnotificação exige validação em campo.</div>
      <h3>Comparação com o mesmo esforço</h3><table><tr><th>Critério</th><th>km de via selecionada</th><th>% dos chamados de 2026</th></tr>${r.comparacao_esforco.map(o => `<tr><td>${esc(o.criterio)}</td><td>${nf(o.via_selecionada_km)}</td><td>${nf(o.pct_chamados_2026_capturados)}</td></tr>`).join("")}</table><p class="nota">Mesmo universo de hexágonos, cobertura e orçamento de km; último hexágono fracionado somente para a comparação analítica. ${esc(r.comparacao_esforco[0]?.limite)}</p>
      <p class="nota">Atendimento em 30 dias: denominador restrito a demandas com pelo menos 30 dias de observação até 19/06/2026.</p>
      <h3>Quem fica no topo, por quartil de valor do terreno (V0)</h3><table><tr><th>Quartil</th><th class="n">Hexágonos</th><th class="n">No topo do score</th><th class="n">No topo do histórico</th></tr>${r.auditoria_quartil.map(o => `<tr><td>${esc(o.quartil_v0)}</td><td class="n">${o.hexagonos}</td><td class="n">${o.no_topo_score}</td><td class="n">${o.no_topo_historico}</td></tr>`).join("")}</table>
      <h3>Resíduo na beira da água (demandas com coordenada)</h3><table><tr><th>Categoria</th><th class="n">% na faixa</th><th class="n">% do km de via na faixa</th><th class="n">Observado ÷ esperado</th></tr>${r.agua.map(o => `<tr><td>${esc(o.categoria)}</td><td class="n">${nf(o.pct_na_faixa)}</td><td class="n">${nf(o.pct_km_de_via_na_faixa)}</td><td class="n">${nf(o.observado_sobre_esperado, 2)}</td></tr>`).join("")}</table><p class="nota">${esc(r.agua[0]?.limite || "")}</p>
      <h3>Pedidos de drenagem × tubulação na camada</h3><table><tr><th>RPA</th><th class="n">Pedidos localizados</th><th class="n">% em hexágono sem tubulação</th><th class="n">% não atendido</th></tr>${r.drenagem.map(o => `<tr><td>${nf(o.rpa, 0)}</td><td class="n">${nf(o.pedidos_localizados, 0)}</td><td class="n">${nf(o.pct_em_hex_sem_tubulacao)}</td><td class="n">${nf(o.pct_nao_atendido)}</td></tr>`).join("")}</table><p class="nota">${esc(r.drenagem[0]?.limite || "")}</p>`);
  },
  async eventos() {
    const r = await api("/api/gestor/eventos");
    P(`<h2>Eventos (trilha de auditoria)</h2><div class="caixa-nota">${esc(r.nota)} Os eventos chegam um a um quando alguém age; não é streaming. As fontes públicas entram em lote pelo pipeline.</div>
      <table><tr><th>Tipo</th><th>Origem</th><th class="n">Quantidade</th></tr>${r.contagem.map(c => `<tr><td>${esc(c.tipo)}</td><td>${c.origem === "simulado" ? etqExemplo(1) : "uso local"}</td><td class="n">${c.n}</td></tr>`).join("")}</table>
      <table><tr><th>Quando (UTC)</th><th>Tipo</th><th>Origem</th><th>Referência</th></tr>${r.eventos.map(e => `<tr><td>${esc(e.criado_em.slice(0, 19).replace("T", " "))}</td><td>${esc(e.tipo)}</td><td>${esc(e.origem)}</td><td><small>${esc(e.ref_id)}</small></td></tr>`).join("")}</table>`);
  },
  async b2b() {
    const r = await api("/api/b2b/demo");
    P(`<h2>Logística reversa e ESG <span class="etq prov">${esc(r.rotulo)}</span></h2>
      ${r.avisos.map(a => `<div class="caixa-nota alerta">${esc(a)}</div>`).join("")}
      <h3>O que os dados públicos já mostram</h3><p>${r.locais_listados} locais de cooperativas, ecopontos e parceiros em listas públicas históricas, por material:</p>
      <table><tr><th>Material</th><th class="n">Locais listados</th></tr>${Object.entries(r.parceiros_por_material_listas_publicas).map(([m, n]) => `<tr><td>${esc(m)}</td><td class="n">${n}</td></tr>`).join("")}</table>
      <h3>O que o app acrescentaria</h3><table><tr><th>Categoria reportada</th><th>Origem</th><th class="n">Reportes</th></tr>${r.reportes_por_categoria.map(c => `<tr><td>${esc(c.categoria)}</td><td>${c.origem === "simulado" ? etqExemplo(1) : "uso local"}</td><td class="n">${c.n}</td></tr>`).join("")}</table>
      <h3>Ideia de produto (não implementada)</h3><p>Relatórios de rastreabilidade com os dados do próprio cliente e mapa de parceiros confirmados pelos cidadãos. Depende das entrevistas B2B.</p>`);
  },
};
function htmlContexto(c) {
  const col = c.coleta_regular;
  return `<p><b>${esc(c.bairro || "Sem bairro de referência")}</b>${c.beira_de_rio ? ' <span class="etq">beira de rio</span>' : ""}${c.zona_de_silencio ? ' <span class="etq">área de missão</span>' : ""}</p>
    <p><b>Quando passa o caminhão:</b> ${col ? `${esc(col.frequencia)}, ${esc(String(col.turno).toLowerCase())} (${col.dias.map(esc).join(", ")}). Hoje ${col.hoje_tem_coleta ? "tem" : "<b>não tem</b>"} coleta no plano.` : "sem setor de coleta na fonte para este ponto."}</p>
    <p><b>Coleta seletiva:</b> ${c.coleta_seletiva ? `${esc(String(c.coleta_seletiva.dia).toLowerCase())}, ${esc(String(c.coleta_seletiva.turno).toLowerCase())}` : "sem setor de seletiva na fonte."}</p>
    <p><b>Nesta área o problema:</b> ${esc(c.nivel_reincidencia)}.</p>
    ${c.descarte_mais_proximo ? `<p><b>Ponto de descarte mais próximo:</b> ${esc(c.descarte_mais_proximo.nome)} (${esc(c.descarte_mais_proximo.tipo)}), a cerca de ${Math.round(c.descarte_mais_proximo.distancia_m)} m em linha reta. <small>${esc(c.descarte_mais_proximo.situacao)}</small></p>` : ""}
    <p class="nota">${col ? esc(col.nota) + ". " : ""}${esc(c.nota_reincidencia)}. Fontes: ${esc(c.fontes)}.</p>`;
}

/* ---------------- partida ---------------- */
(async () => {
  S.token = sessionStorage.getItem("ws_token");
  try { if (S.token) return await iniciarApp(); } catch (e) { S.token = null; }
  tentar(telaEntrada)();
})();
