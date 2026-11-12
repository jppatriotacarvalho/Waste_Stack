"""Tabelas Gold: cada função lê só a Silver e grava em data/gold/ com fonte e ano.

Regras de exibição (aprovadas em 10/11/2026) ficam registradas nas próprias tabelas:
- `uso`: "gestor" (interno) ou "publico" (pode ir ao App Cidadão).
- A versão do 156 sem NUMERO não permite contar endereços distintos. Por isso nenhuma contagem do 156
  por rua ou hexágono é marcada como pública: o app recebe só níveis ("baixo", "médio", "alto").
"""
import json

import duckdb
import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely import union_all

from .arquivos import json_salvar, ler, salvar
from .config import EPSG_METROS, GOLD, H3_RES, SILVER
from .silver import MATERIAIS, RESTRITO_SILVER, _SEM, _dominio


def _so_linhas(g):
    """Mapa base de vias: guarda só as partes de linha. Recortes podem gerar GeometryCollection com pontos
    soltos, que o mapa da interface desenharia como marcadores falsos."""
    import shapely
    from shapely.geometry import MultiLineString
    if g is None or g.geom_type in ("LineString", "MultiLineString"):
        return g
    linhas = []
    for parte in shapely.get_parts(g):
        if parte.geom_type == "LineString":
            linhas.append(parte)
        elif parte.geom_type == "MultiLineString":
            linhas.extend(parte.geoms)
    return MultiLineString(linhas) if linhas else None

F156 = "Central 156 (Emlurb), jan/2024–19/06/2026, versão sem NUMERO"
MESES_COMPLETOS = 25


def g_salvar(frame, nome, fontes, nota="", uso="gestor"):
    frame = frame.copy()
    frame["uso"] = uso
    return salvar(frame, nome, camada=GOLD, fontes=fontes, nota=nota)


def g_ler(nome, geo=False):
    return ler(nome, camada=GOLD, geo=geo)


def menos_de_5(serie):
    """Texto de exibição pública: contagens pequenas viram "menos de 5"."""
    return serie.map(lambda v: "menos de 5" if pd.notna(v) and 0 < v < 5 else ("0" if pd.notna(v) and v == 0 else (str(int(round(v))) if pd.notna(v) else "")))


# ====================================================================== 20: Gold de fonte única
def gold_fonte_unica():
    r = {}
    # G-U1 orçamento
    o = ler("orcamento_acao")
    pop = int(ler("populacao_bairro").populacao.sum())
    total = o.groupby("ano").valor.sum().rename("total_loa")
    z = o[o.zeladoria != ""].merge(total, on="ano")
    z["pct_loa"] = 100 * z.valor / z.total_loa
    z["valor_1pct_eficiencia"] = z.valor / 100
    z["valor_por_habitante_censo2022"] = z.valor / pop
    z["fonte_dado"] = "PPA e LOA 2023–2024 (dotação inicial: previsto, não realizado); população do Censo 2022"
    g_salvar(z, "mercado_orcamento_zeladoria", ["orcamento_acao", "populacao_bairro"],
             "1% de eficiência é uma conta de ordem de grandeza, não economia comprovada")
    r["limpeza_urbana_2024_mi"] = round(float(z[(z.ano == 2024) & z.zeladoria.str.startswith("limpeza")].valor.sum()) / 1e6, 1)

    # G-U2 calendário dos polos
    p = ler("polo_comercio", geo=True)
    linhas = []
    for polo, g in p.groupby("polo_id"):
        c = gpd.GeoSeries([g.geometry.union_all().centroid], crs=EPSG_METROS).to_crs(4326).iloc[0]
        for i, d in enumerate(_SEM):
            conhecidos = g[g.dias_conhecidos]
            linhas.append({"polo_id": polo, "equipamentos": " + ".join(g.nome), "tipos": "+".join(sorted(set(g.tipo))),
                           "dia_semana": i, "dia": d, "aberto": bool(conhecidos[d].fillna(False).any()) if len(conhecidos) else None,
                           "equipamentos_abertos": int(conhecidos[d].fillna(False).sum()),
                           "tem_equipamento_sem_dia_na_fonte": bool((~g.dias_conhecidos).any()),
                           "coordenada_suspeita": bool(g.coordenada_suspeita.any()), "lat": c.y, "lon": c.x})
    cal = pd.DataFrame(linhas)
    cal["fonte_dado"] = "CONVIVA — feiras e mercados (cadastro parcial; recebido em 11/11/2026)"
    g_salvar(cal, "calendario_polos_comercio", ["polo_comercio"], uso="publico")
    r["polos"] = int(cal.polo_id.nunique())

    # G-U3 setores, G-U4 faixas
    s = ler("setor_coleta", geo=True)
    s["geometry"] = s.geometry.simplify(5)
    g_salvar(s, "camada_setores_coleta", ["setor_coleta"], "vigência dos setores a confirmar com a Emlurb", uso="publico")
    f = ler("faixa_marginal_simplificada", geo=True)
    g_salvar(f, "camada_faixas_marginais", ["faixa_marginal"], uso="publico")

    # G-U5 CIS (gestor): CIS pequenas saem da camada nominal e ficam só no agregado por bairro
    c = ler("cis", geo=True)
    grandes = c[~c.cis_pequena].copy()
    grandes["geometry"] = grandes.geometry.simplify(3)
    g_salvar(grandes, "camada_cis_gestor", ["cis"], "Atlas 2014; acesso só do gestor; sem ranking nominal")
    peq = c[c.cis_pequena].groupby("bairro_predominante").agg(cis_pequenas=("cis_geocod", "size")).reset_index()
    peq["fonte_dado"] = "Atlas das CIS, 2014 (CIS com menos de 50 imóveis, só agregadas)"
    g_salvar(peq, "cis_pequenas_agregado", ["cis"])
    r["cis_camada"], r["cis_pequenas_agregadas"] = int(len(grandes)), int(c.cis_pequena.sum())

    # G-U6 Defesa Civil: só agregados por bairro × mês e por dia
    d = pd.read_parquet(RESTRITO_SILVER / "acao_defesa_civil.parquet")
    d = d[d.data.dt.year.isin([2023, 2024])]
    d["mes"] = d.data.dt.to_period("M").astype(str)
    d["rua"] = d.bairro_chave.fillna("") + "|" + d.logradouro_norm.fillna("")
    b = d[d.bairro_casado].groupby(["bairro_chave", "mes"]).agg(
        acoes=("grupo", "size"), acoes_lona=("grupo", lambda s: int((s == "lona").sum())), area_lona_m2=("area_lona_m2", "sum"),
        acoes_zeladoria=("grupo", lambda s: int((s == "zeladoria").sum())),
        acoes_sociais=("grupo", lambda s: int((s == "social").sum()))).reset_index()
    alto = d[d.bairro_casado & d.grau_risco.isin(["R3", "R4"])].groupby(["bairro_chave", "mes"]).rua.nunique().rename("ruas_r3_r4")
    b = b.merge(alto, on=["bairro_chave", "mes"], how="left").fillna({"ruas_r3_r4": 0})
    b["fonte_dado"] = "Defesa Civil do Recife, 2023–2024 (presença do poder público, não demanda; dados param em 30/12/2024)"
    g_salvar(b, "risco_encosta_bairro_mes", ["acao_defesa_civil (restrito)"], "agregado bairro × mês; nunca rua × dia")
    ev = d.groupby(d.data.dt.date).agg(alagamento=("grupo", lambda s: int((s == "alagamento").sum())),
                                       deslizamento=("grupo", lambda s: int((s == "deslizamento").sum())),
                                       bairros_com_evento=("bairro_chave", "nunique")).reset_index().rename(columns={"data": "dia"})
    ev = ev[(ev.alagamento > 0) | (ev.deslizamento > 0)]
    ev["dia"] = pd.to_datetime(ev.dia)
    ev["fonte_dado"] = "Defesa Civil 2023–2024; não é série de chuva nem mapa de alagamentos"
    g_salvar(ev, "calendario_eventos_chuva", ["acao_defesa_civil (restrito)"])
    r["dias_com_alagamento"] = int((ev.alagamento > 0).sum())

    # G-U7 mapa base e perfil de serviço
    bairros = ler("territorio_bairro", geo=True)
    mb = bairros[["bairro_codigo", "bairro_chave", "bairro_nome", "rpa", "area_km2", "geometry"]].copy()
    mb["geometry"] = mb.geometry.simplify(8)
    g_salvar(mb, "mapa_base_bairros", ["territorio_bairro"], uso="publico")
    vias = ler('territorio_logradouro', geo=True)
    vias = vias[vias.ligacao_geometrica_valida][['logradouro_codigo', 'logradouro_norm', 'geometry']].copy()
    vias['geometry'] = vias.geometry.simplify(3).map(_so_linhas)
    g_salvar(vias, 'mapa_base_vias', ['territorio_logradouro'], 'Mapa local sem serviço externo de imagens', uso='publico')
    dominio = gpd.GeoDataFrame({'politica': ['união IBGE 2025 + bairros; divergências preservadas']},
                              geometry=[_dominio()], crs=EPSG_METROS)
    g_salvar(dominio, 'dominio_demonstracao', ['contorno_municipal_ibge_2025', 'territorio_bairro'], uso='publico')
    agua = ler("territorio_agua", geo=True)
    agua = agua[agua.usar_distancia][["agua_id", "nome", "classe", "geometry"]].copy()
    agua["geometry"] = agua.geometry.simplify(3)
    g_salvar(agua, "mapa_base_agua", ["territorio_agua"], uso="publico")
    faces = ler("face_quadra")
    pf = faces.groupby("bairro_codigo").agg(faces=("dsqf", "size"), pct_coleta_manual=("coleta_manual", "mean"),
                                           pct_limpeza_mensal_ou_sem=("limpeza_mensal_ou_sem", "mean"), pct_sem_galeria=("sem_galeria", "mean"),
                                           pct_sem_esgoto=("sem_esgoto", "mean"), pct_escadaria=("escadaria", "mean"),
                                           v0_mediano=("v0", "median"), pct_v0_ausente=("v0", lambda s: s.isna().mean())).reset_index()
    for col in [c_ for c_ in pf.columns if c_.startswith("pct_")]:
        pf[col] = 100 * pf[col]
    pf = pf.merge(pd.DataFrame(bairros[["bairro_codigo", "bairro_nome", "rpa"]]), on="bairro_codigo", how="left")
    pf["fonte_dado"] = "Área urbana — faces de quadra (data desconhecida; plano oficial, vigência não confirmada)"
    g_salvar(pf, "perfil_servico_bairro", ["face_quadra"])

    # G-U8 linha de base do 156
    o156 = ler("ocorrencia_156")
    rec = o156[o156.no_recorte & o156.periodo_analise].copy()
    rec["mes"] = rec.data_demanda.dt.to_period("M").astype(str)
    comp = rec[rec.mes_completo]
    comp = comp.copy()
    comp['elegivel_30d'] = comp.data_demanda <= pd.Timestamp('2026-06-19') - pd.Timedelta(days=30)
    comp['atendido_30d_elegivel'] = comp.elegivel_30d & comp.dias_ate_atender.between(0, 30)
    dm = comp.groupby(["categoria", "rpa", "mes"]).agg(
        chamados=("categoria", "size"), atendidos=("situacao", lambda s: int((s == "ATENDIDA").sum())),
        elegiveis_30d=('elegivel_30d', 'sum'),
        atendidos_30d=('atendido_30d_elegivel', 'sum'), mediana_dias=("dias_ate_atender", "median")).reset_index()
    dm["pct_atendido_30d"] = 100 * dm.atendidos_30d / dm.elegiveis_30d.replace(0, np.nan)
    dm["fonte_dado"] = F156 + "; chamados registrados, não toneladas"
    g_salvar(dm, "base156_desempenho_mensal", ["ocorrencia_156"], "só meses completos")
    ref = pd.Timestamp("2026-06-19")
    aberta = rec[~rec.situacao.isin(["ATENDIDA", "CANCELADA", "IMPROCEDENTE", "INDEFERIDA"])].copy()
    aberta["idade_dias"] = (ref - aberta.data_demanda).dt.days
    aberta["faixa_idade"] = pd.cut(aberta.idade_dias, [-1, 30, 90, 180, 100000], labels=["até 30 dias", "31 a 90", "91 a 180", "mais de 180"]).astype(str)
    # Como na análise do 156 (pasta 08): "aberta" = ainda se move no arquivo de 2026; as demais estão paradas desde 2024–2025.
    aberta["movimento"] = np.where(aberta.arquivo_ano == 2026, "aberta com movimento em 2026", "parada desde 2024–2025")
    fila = aberta.groupby(["categoria", "rpa", "situacao", "faixa_idade", "movimento"]).size().rename("pedidos").reset_index()
    fila["referencia"] = "situação em 19/06/2026 (último dia do arquivo)"
    fila["fonte_dado"] = F156
    g_salvar(fila, "base156_fila_aberta", ["ocorrencia_156"])
    rl = comp.groupby(["logradouro_norm", "bairro_chave"]).agg(
        chamados=("categoria", "size"), meses_com_chamado=("mes", "nunique"),
        categoria_dominante=("categoria", lambda s: s.value_counts().index[0])).reset_index()
    rl = rl[rl.chamados >= 10]
    rl["meses_no_periodo"] = MESES_COMPLETOS
    rl["publicavel"] = False
    rl["motivo_bloqueio"] = "regra 10+ endereços não verificável sem a coluna NUMERO: uso interno do gestor"
    rl["fonte_dado"] = F156
    g_salvar(rl, "base156_reincidencia_logradouro", ["ocorrencia_156"], "10+ chamados; publicação bloqueada")
    r.update(recorte=int(len(rec)), recorte_meses_completos=int(len(comp)), ruas_10_mais=int(len(rl)),
             ruas_20_de_25_meses=int((rl.meses_com_chamado >= 20).sum()),
             drenagem_aberta=int(aberta[aberta.categoria == "Limpeza de drenagem"].shape[0]),
             drenagem_aberta_com_movimento_2026=int(((aberta.categoria == "Limpeza de drenagem") & (aberta.arquivo_ano == 2026)).sum()),
             drenagem_parada_2024_2025=int(((aberta.categoria == "Limpeza de drenagem") & (aberta.arquivo_ano != 2026)).sum()),
             recorte_aberto_com_movimento_2026=int((aberta.arquivo_ano == 2026).sum()))
    json_salvar(GOLD / "validacao_fonte_unica.json", r)
    return r


# ====================================================================== 21: localizar o 156 e histórico por hexágono
def _logradouro_h3():
    """Reparte cada rua (dentro do bairro) pelos hexágonos que ela cruza, pela fração do comprimento."""
    rel = ler("territorio_logradouro", geo=True)
    rel = rel[rel.ligacao_geometrica_valida][["logradouro_codigo", "bairro_codigo", "bairro_chave", "logradouro_norm", "nome_ambiguo", "comprimento_m", "geometry"]]
    grade = ler("grade_h3", geo=True)[["h3", "geometry"]]
    cruz = gpd.overlay(rel, grade, how="intersection", keep_geom_type=True)
    cruz["m_no_hex"] = cruz.length
    cruz = cruz[cruz.m_no_hex > 0.5]
    tot = cruz.groupby(["logradouro_codigo", "bairro_codigo"]).m_no_hex.transform("sum")
    cruz["peso"] = cruz.m_no_hex / tot
    cruz["n_hex"] = cruz.groupby(["logradouro_codigo", "bairro_codigo"]).h3.transform("nunique")
    out = pd.DataFrame(cruz.drop(columns="geometry"))
    salvar(out, "logradouro_h3", fontes=["territorio_logradouro", "grade_h3"],
           nota="fração do comprimento da rua (no bairro) em cada hexágono; base da localização por rua")
    return out


def localizar_156():
    """S4: leva cada demanda do 156 à grade H3, registrando método e precisão. Não cria ponto fictício.

    - coordenada válida dentro do domínio (IBGE ∪ bairros): hexágono do ponto, peso 1;
    - sem coordenada: rua + bairro casados com a relação oficial (nome não ambíguo); a demanda é repartida
      pelos hexágonos que a rua cruza no bairro (pesos somam 1) e a precisão é o comprimento da rua;
    - o resto fica sem localização, com o motivo.
    """
    occ = ler("ocorrencia_156").reset_index().rename(columns={"index": "ocorrencia_id"})
    lh = _logradouro_h3()
    grade = set(ler("grade_h3").h3)
    dominio = _dominio()
    com = occ[occ.coord_valida].copy()
    pts = gpd.GeoDataFrame(com[["ocorrencia_id"]], geometry=gpd.points_from_xy(com.lon, com.lat), crs=4326).to_crs(EPSG_METROS)
    dentro = pts.within(dominio).values
    com["h3"] = [h3.latlng_to_cell(la, lo, H3_RES) for la, lo in zip(com.lat, com.lon)]
    ok_ponto = dentro & com.h3.isin(grade).values
    por_ponto = com.loc[ok_ponto, ["ocorrencia_id", "h3"]].assign(peso=1.0, metodo="coordenada", n_hex=1, precisao_m=0.0)
    fora_dominio = set(com.loc[~ok_ponto, "ocorrencia_id"])

    resto = occ[~occ.ocorrencia_id.isin(por_ponto.ocorrencia_id)]
    ruas = lh[~lh.nome_ambiguo]
    chave = ruas.drop_duplicates(["bairro_chave", "logradouro_norm", "logradouro_codigo"])
    unico = chave.groupby(["bairro_chave", "logradouro_norm"]).logradouro_codigo.nunique()
    validas = unico[unico == 1].reset_index()[["bairro_chave", "logradouro_norm"]]
    ruas = ruas.merge(validas, on=["bairro_chave", "logradouro_norm"])
    j = resto[["ocorrencia_id", "bairro_chave", "logradouro_norm"]].merge(
        ruas[["bairro_chave", "logradouro_norm", "h3", "peso", "n_hex", "comprimento_m"]], on=["bairro_chave", "logradouro_norm"])
    por_rua = j[["ocorrencia_id", "h3", "peso", "n_hex"]].assign(metodo="logradouro+bairro", precisao_m=j.comprimento_m.values)
    loc = pd.concat([por_ponto, por_rua], ignore_index=True)
    salvar(loc, "ocorrencia_156_localizada", fontes=["ocorrencia_156", "territorio_logradouro", "grade_h3"],
           nota="grão: demanda × hexágono; pesos somam 1 por demanda; precisao_m = comprimento da rua no bairro (0 = coordenada)")

    # diagnóstico de quem ficou sem lugar
    achados = set(loc.ocorrencia_id)
    sem = occ[~occ.ocorrencia_id.isin(achados)].copy()
    bairros = set(ler("territorio_bairro", geo=True).bairro_chave)
    nomes_rua = set(lh.logradouro_norm)
    pares_amb = set(map(tuple, lh[lh.nome_ambiguo][["bairro_chave", "logradouro_norm"]].drop_duplicates().values))
    def motivo(b, l):
        if b not in bairros:
            return "bairro não informado ou não reconhecido"
        if (b, l) in pares_amb:
            return "nome de rua ambíguo no bairro"
        if l in nomes_rua:
            return "rua existe, mas não neste bairro (relação oficial)"
        return "rua não encontrada na Área urbana"
    sem["motivo"] = [motivo(b, l) for b, l in zip(sem.bairro_chave, sem.logradouro_norm)]
    occ["localizada"] = occ.ocorrencia_id.isin(achados)
    metodo = loc.drop_duplicates("ocorrencia_id").set_index("ocorrencia_id").metodo
    occ["metodo"] = occ.ocorrencia_id.map(metodo).fillna("sem localização")
    rec = occ[occ.no_recorte & occ.periodo_analise]
    cob_bairro = rec[rec.bairro_chave.isin(bairros)].groupby("bairro_chave").agg(
        demandas=("localizada", "size"), pct_localizada=("localizada", "mean"),
        pct_por_coordenada=("metodo", lambda s: (s == "coordenada").mean())).reset_index()
    cob_bairro[["pct_localizada", "pct_por_coordenada"]] *= 100
    perfil = ler('face_quadra').groupby('bairro_codigo').v0.median().rename('v0_mediano').reset_index()
    quartis = {}
    if perfil is not None:
        tb = pd.DataFrame(ler("territorio_bairro", geo=True)[["bairro_codigo", "bairro_chave"]]).merge(perfil[["bairro_codigo", "v0_mediano"]], on="bairro_codigo")
        cob_bairro = cob_bairro.merge(tb[["bairro_chave", "v0_mediano"]], on="bairro_chave", how="left")
        cob_bairro["quartil_v0"] = pd.qcut(cob_bairro.v0_mediano, 4, labels=["Q1 (menor V0)", "Q2", "Q3", "Q4 (maior V0)"]).astype(str)
        q = cob_bairro.groupby("quartil_v0").apply(lambda g: pd.Series({
            "pct_localizada": float((g.pct_localizada * g.demandas).sum() / g.demandas.sum()),
            "pct_por_coordenada": float((g.pct_por_coordenada * g.demandas).sum() / g.demandas.sum())}))
        quartis = q.round(1).to_dict("index")
    cob_bairro["fonte_dado"] = F156
    salvar(cob_bairro, "cobertura_localizacao_156_bairro", fontes=["ocorrencia_156_localizada"], nota="recorte WasteStack")
    resumo = {"demandas": int(len(occ)), "localizadas": int(occ.localizada.sum()), "pct_localizadas": round(100 * occ.localizada.mean(), 1),
              "recorte": int(len(rec)), "recorte_pct_localizado": round(100 * rec.localizada.mean(), 1),
              "recorte_por_metodo_pct": (100 * rec.metodo.value_counts(normalize=True)).round(1).to_dict(),
              "coordenadas_fora_do_dominio_ou_da_grade": len(fora_dominio),
              "motivos_sem_localizacao_recorte": sem[sem.no_recorte & sem.periodo_analise].motivo.value_counts().to_dict(),
              "precisao_rua_mediana_m": float(por_rua.drop_duplicates("ocorrencia_id").precisao_m.median()),
              "hexagonos_por_demanda_localizada_por_rua_mediana": float(por_rua.drop_duplicates("ocorrencia_id").n_hex.median()),
              "cobertura_por_quartil_v0_do_bairro": quartis,
              "limite": "localização por rua reparte a demanda ao longo da rua; não é o ponto do chamado"}
    json_salvar(SILVER / "validacao_localizacao_156.json", resumo)
    return resumo


def historico_h3_mes():
    """G-I1: chamados do recorte localizados, por hexágono × mês × categoria (soma de pesos), só meses completos."""
    con = duckdb.connect()
    q = f"""
      select l.h3, strftime(o.data_demanda, '%Y-%m') as mes, o.categoria,
             sum(l.peso) as chamados, sum(case when l.metodo='coordenada' then l.peso else 0 end) as chamados_por_coordenada
      from read_parquet('{(SILVER / 'ocorrencia_156_localizada.parquet').as_posix()}') l
      join (select row_number() over () - 1 as ocorrencia_id, * from read_parquet('{(SILVER / 'ocorrencia_156.parquet').as_posix()}')) o
        using (ocorrencia_id)
      where o.no_recorte and o.mes_completo
      group by 1, 2, 3 order by 1, 2, 3"""
    h = con.execute(q).df()
    con.close()
    h["fonte_dado"] = F156 + "; localização por coordenada ou por rua+bairro"
    g_salvar(h, "motor_historico_h3_mes", ["ocorrencia_156_localizada"], "soma de pesos (frações de demanda); só os 25 meses completos")
    return {"linhas": int(len(h)), "hexagonos_com_historico": int(h.h3.nunique()), "chamados_somados": round(float(h.chamados.sum()), 1),
            "meses": int(h.mes.nunique())}


# ====================================================================== 22: feature store por hexágono
def _rank(s, universo):
    """Posto percentual (0–1) dentro do universo; fora dele fica NaN."""
    return s.where(universo).rank(pct=True)


def features_h3():
    """G-I3: o que se sabe de cada hexágono antes do primeiro reporte. Cada coluna herda o ano da sua fonte."""
    grade = ler("grade_h3", geo=True)
    g = grade[["h3", "bairro_codigo", "rpa", "fracao_bairros", "borda_divergente", "geometry"]].copy()
    cent = g.geometry.centroid
    g["x_m"], g["y_m"] = cent.x, cent.y
    ll = gpd.GeoSeries(cent, crs=EPSG_METROS).to_crs(4326)
    g["lat"], g["lon"] = ll.y.values, ll.x.values
    area = g.area
    lh = ler("logradouro_h3")
    g["via_m"] = g.h3.map(lh.groupby("h3").m_no_hex.sum()).fillna(0.0)

    # faces de quadra -> rua no bairro -> hexágonos (a face não tem geometria; a rua é o elo documentado)
    faces = ler("face_quadra")
    fa = faces.groupby(["logradouro_codigo", "bairro_codigo"]).agg(
        n=("dsqf", "size"), manual=("coleta_manual", "sum"), limp=("limpeza_mensal_ou_sem", "sum"), semgal=("sem_galeria", "sum"),
        semesg=("sem_esgoto", "sum"), esc=("escadaria", "sum"), v0n=("v0", "count"), v0s=("v0", "sum")).reset_index()
    fh = lh[["logradouro_codigo", "bairro_codigo", "h3", "peso"]].merge(fa, on=["logradouro_codigo", "bairro_codigo"])
    cols = ["n", "manual", "limp", "semgal", "semesg", "esc", "v0n", "v0s"]
    fh[cols] = fh[cols].multiply(fh.peso, axis=0)
    a = fh.groupby("h3")[cols].sum()
    g = g.merge(pd.DataFrame({"faces_equiv": a.n, "pct_coleta_manual": a.manual / a.n, "pct_limpeza_mensal_ou_sem": a.limp / a.n,
                              "pct_sem_galeria": a.semgal / a.n, "pct_sem_esgoto_face": a.semesg / a.n, "pct_escadaria": a.esc / a.n,
                              "v0_medio": (a.v0s / a.v0n).where(a.v0n > 0)}).reset_index(), on="h3", how="left")

    # água: faixa marginal legal
    faixa = ler("faixa_marginal_uniao", geo=True).geometry.iloc[0]
    g["pct_faixa"] = (g.intersection(faixa).area / area).values
    g["dist_faixa_m"] = cent.distance(faixa).values

    # CIS (Atlas 2014)
    cis = ler("cis", geo=True)[["cis_geocod", "unidade_paisagem", "nota_coleta_lixo", "deficit_coleta", "geometry"]]
    ov = gpd.overlay(g[["h3", "geometry"]], cis, how="intersection", keep_geom_type=True)
    ov["a"] = ov.area
    hex_area = dict(zip(g.h3, area))
    c1 = ov.groupby("h3").apply(lambda t: pd.Series({
        "pct_cis": t.a.sum() / hex_area[t.name], "pct_cis_deficit_coleta": t.a[t.deficit_coleta].sum() / hex_area[t.name],
        "cis_nota_coleta": float(np.average(t.nota_coleta_lixo.fillna(0), weights=t.a)),
        "cis_unidade": t.groupby("unidade_paisagem").a.sum().idxmax()})).reset_index()
    g = g.merge(c1, on="h3", how="left").fillna({"pct_cis": 0.0, "pct_cis_deficit_coleta": 0.0})

    # redes: comprimento atribuído ao hexágono do ponto médio do trecho (trechos curtos; erro ≤ tamanho do trecho)
    for nome, col in (("rede_drenagem", "drenagem_m"), ("rede_esgoto", "esgoto_2022_m")):
        r = ler(nome, geo=True)
        meio = gpd.GeoDataFrame({"m": r.comprimento_m.values}, geometry=r.geometry.interpolate(0.5, normalized=True).values, crs=EPSG_METROS)
        j = gpd.sjoin(meio, g[["h3", "geometry"]], predicate="within")
        g[col] = g.h3.map(j.groupby("h3").m.sum()).fillna(0.0)

    # setores de coleta no centro do hexágono
    setores = ler("setor_coleta", geo=True)
    pc = gpd.GeoDataFrame(g[["h3"]], geometry=cent.values, crs=EPSG_METROS)
    for tipo, pref in (("regular", "coleta"), ("seletiva", "seletiva")):
        j = gpd.sjoin(pc, setores[setores.tipo == tipo][["setor", "frequencia", "turno", "dias_coleta", "geometry"]], predicate="within")
        j = j[~j.h3.duplicated()].set_index("h3")
        g[pref + "_setor"] = g.h3.map(j.setor)
        g[pref + "_frequencia"] = g.h3.map(j.frequencia)
        g[pref + "_turno"] = g.h3.map(j.turno)
        g[pref + "_dias"] = g.h3.map(j.dias_coleta)
    g["tem_seletiva"] = g.seletiva_setor.notna()

    # descarte e polos
    pd_ = ler("ponto_descarte", geo=True)
    pd_ = pd_[pd_.geometry.notna()]
    for col, sub in (("dist_descarte_m", pd_), ("dist_entulho_m", pd_[pd_.entulho]), ("dist_reciclaveis_m", pd_[pd_.reciclaveis])):
        j = gpd.sjoin_nearest(pc, sub[["ponto_id", "geometry"]], distance_col="d")
        j = j[~j.h3.duplicated()].set_index("h3")
        g[col] = g.h3.map(j.d)
        if col == "dist_descarte_m":
            g["descarte_mais_proximo"] = g.h3.map(j.ponto_id)
    polos = ler("polo_comercio", geo=True)
    polos = polos[~polos.coordenada_suspeita]
    j = gpd.sjoin_nearest(g[["h3", "geometry"]], polos[["polo_id", "geometry"]], distance_col="d")
    j = j[~j.h3.duplicated()].set_index("h3")
    g["dist_polo_m"] = g.h3.map(j.d)
    g["polo_300m"] = g.dist_polo_m <= 300
    g["polo_id"] = g.h3.map(j.polo_id).where(g.polo_300m)

    # encosta (Defesa Civil 2023–24): grau máximo das ruas R3/R4 levado ao hexágono; nenhuma rua sai nominalmente
    dc = pd.read_parquet(RESTRITO_SILVER / "acao_defesa_civil.parquet")
    ruas = dc[dc.grau_risco.isin(["R3", "R4"])].groupby(["bairro_chave", "logradouro_norm"]).grau_risco.max().reset_index()
    m = ruas.merge(lh[~lh.nome_ambiguo][["bairro_chave", "logradouro_norm", "h3"]], on=["bairro_chave", "logradouro_norm"])
    casadas = m.drop_duplicates(["bairro_chave", "logradouro_norm"]).shape[0]
    g["encosta_grau_max"] = g.h3.map(m.groupby("h3").grau_risco.max())
    g["encosta_ruas_r4"] = g.h3.map(m[m.grau_risco == "R4"].groupby("h3").logradouro_norm.nunique()).fillna(0).astype(int)
    g["encosta_ruas_r3"] = g.h3.map(m[m.grau_risco == "R3"].groupby("h3").logradouro_norm.nunique()).fillna(0).astype(int)
    bd = dc[dc.bairro_casado].groupby("bairro_chave").agg(dc_registros=("regional_morro", "size"), dc_pct_morro=("regional_morro", "mean")).reset_index()
    tb = pd.DataFrame(ler("territorio_bairro", geo=True)[["bairro_codigo", "bairro_chave", "bairro_nome"]])
    g = g.merge(tb, on="bairro_codigo", how="left").merge(bd, on="bairro_chave", how="left")

    # regime territorial: contagem de evidências independentes; sem evidência = indefinido (não se atribui em silêncio)
    # Só evidências do próprio hexágono. A regional da Defesa Civil por bairro fica como coluna de contexto
    # (dc_pct_morro), fora da regra: quase todo bairro com registro cairia em "morro".
    ev_m = ((g.pct_coleta_manual >= 0.3).astype(int) + (g.pct_escadaria > 0).astype(int) + g.encosta_grau_max.notna().astype(int)
            + g.cis_unidade.eq("Colinas").astype(int))
    ev_p = (((g.pct_faixa > 0.05) | (g.dist_faixa_m < 50)).astype(int) + g.cis_unidade.isin(["Planície", "Baixo Estuário"]).astype(int))
    g["evid_morro"], g["evid_planicie"] = ev_m, ev_p
    g["regime"] = np.select([(ev_m > ev_p), (ev_p > ev_m)], ["morro", "planicie"], "indefinido")
    g["regime_confianca"] = np.select([(ev_m - ev_p).abs() >= 2, (ev_m - ev_p).abs() == 1], ["maior", "menor"], "sem base")
    g["regime_nota"] = "regra provisória por evidências (faces, Defesa Civil 2023–24, CIS 2014, faixa); sem dado de relevo"
    g["fonte_dado"] = "Área urbana (faces sem data), Faixas, CIS 2014, Núcleos (esgoto 2022), Defesa Civil 2023–24, descarte (listas históricas)"
    g_salvar(g, "motor_features_h3", ["grade_h3", "face_quadra", "faixa_marginal", "cis", "redes", "setor_coleta", "ponto_descarte", "polo_comercio", "defesa civil"],
             "encosta_grau_max e CIS: uso do gestor")
    com_via = g.via_m > 0
    return {"hexagonos": int(len(g)), "com_via": int(com_via.sum()), "regime": g.regime.value_counts().to_dict(),
            "regime_entre_hex_com_via": g[com_via].regime.value_counts().to_dict(),
            "ruas_r3r4_casadas": int(casadas), "ruas_r3r4_total": int(len(ruas)),
            "hex_urbanos_sem_drenagem_por_rpa_pct": (100 * g[com_via & (g.via_m >= 200)].assign(sem=lambda t: t.drenagem_m == 0).groupby("rpa")["sem"].mean()).round(1).to_dict(),
            "hex_sem_seletiva_pct": round(100 * (~g[com_via].tem_seletiva).mean(), 1), "hex_com_polo_300m": int(g.polo_300m.sum())}


# ====================================================================== componentes do motor e zonas de silêncio
_MOT_V = {"v_cis": "área em CIS com déficit de coleta (Atlas 2014)", "v_v0": "valor de terreno (V0) entre os mais baixos",
          "v_seletiva": "setor sem coleta seletiva", "v_drenagem": "sem tubulação de drenagem na camada",
          "v_manual": "coleta manual no plano das faces", "v_descarte": "longe de ponto que recebe entulho"}


def _reincidencia(hist, ate_mes=None):
    """Soma dos meses com chamado, com meia-vida de 12 meses contados do fim da janela."""
    h = hist if ate_mes is None else hist[hist.mes <= ate_mes]
    m = h.groupby(["h3", "mes"]).chamados.sum().reset_index()
    fim = pd.Period(m.mes.max(), "M")
    idade = m.mes.map(lambda t: (fim - pd.Period(t, "M")).n)
    m["v"] = m.chamados.clip(upper=1) * 0.5 ** (idade / 12)
    return pd.DataFrame({"reinc_valor": m.groupby("h3").v.sum(), "meses_com_chamado": m[m.chamados >= 0.5].groupby("h3").mes.nunique(),
                         "chamados_janela": m.groupby("h3").chamados.sum()}).fillna(0), int(m.mes.nunique())


def componentes_motor(ate_mes=None):
    """Componentes 0–1 do score e marca de zona de silêncio. `ate_mes` limita o 156 (teste sem vazamento)."""
    f = g_ler("motor_features_h3")
    hist = g_ler("motor_historico_h3_mes")
    re_, n_meses = _reincidencia(hist, ate_mes)
    d = f.merge(re_.reset_index(), on="h3", how="left").fillna({"reinc_valor": 0, "meses_com_chamado": 0, "chamados_janela": 0})
    uni = d.via_m > 0
    d["c_reincidencia"] = _rank(d.reinc_valor, uni).where(d.reinc_valor > 0, 0.0).fillna(0.0)
    d["motivo_reincidencia"] = d.meses_com_chamado.map(lambda n: f"chamados do 156 em {int(n)} de {n_meses} meses")
    v = pd.DataFrame({
        "v_cis": (d.pct_cis_deficit_coleta * (0.5 + 0.5 * d.cis_nota_coleta.fillna(0) / 10)).clip(upper=1),
        "v_v0": 1 - _rank(d.v0_medio, uni), "v_seletiva": (~d.tem_seletiva).astype(float),
        "v_drenagem": ((d.drenagem_m == 0) & (d.via_m >= 100)).astype(float), "v_manual": d.pct_coleta_manual,
        "v_descarte": (d.dist_entulho_m / 3000).clip(upper=1)})
    d["c_vulnerabilidade"] = v.mean(axis=1, skipna=True).where(uni, 0.0)
    d["indicadores_vulnerabilidade_disponiveis"] = v.notna().sum(axis=1)
    d["motivo_vulnerabilidade"] = "carência: " + v.fillna(-1).idxmax(axis=1).map(_MOT_V)
    carga = d.encosta_ruas_r4 + 0.5 * d.encosta_ruas_r3
    enc = _rank(carga.where(carga > 0), uni).fillna(0.0)
    e_morro = (0.6 * enc + 0.2 * (d.pct_escadaria > 0) + 0.2 * d.pct_coleta_manual.fillna(0)).clip(upper=1)
    e_plan = (d.pct_faixa / 0.5).clip(upper=1)
    d["c_exposicao"] = np.select([d.regime == "morro", d.regime == "planicie"], [e_morro, e_plan], np.maximum(e_morro, e_plan))
    d["motivo_exposicao"] = np.where(
        (d.regime == "planicie") | ((d.regime == "indefinido") & (e_plan >= e_morro)),
        d.pct_faixa.map(lambda p: f"{100 * p:.0f}% do hexágono em faixa marginal de rio"),
        np.where(enc > 0, "ruas com grau de risco alto de encosta (Defesa Civil 2023–24)", "escadaria ou coleta manual no plano das faces"))
    alternada = d.coleta_frequencia.isin(["SEG-QUA-SEX", "TER-QUI-SAB"])
    d["c_contexto"] = np.maximum(d.polo_300m.astype(float), alternada * 0.5)
    d["motivo_contexto"] = np.where(d.polo_300m, "feira ou mercado a até 300 m", "setor de coleta em dias alternados")

    # zona de silêncio: muita carência esperada e pouco registro por km de via (hipótese a investigar, não prova)
    # "urbanizado" aqui é um proxy: 200 m de via e pelo menos 10 faces de quadra equivalentes cadastradas
    urb = (d.via_m >= 200) & (d.faces_equiv.fillna(0) >= 10)
    d["urbanizado_proxy"] = urb
    d["esperado_rank"] = _rank(d.c_vulnerabilidade, urb)
    d["registrado_por_km"] = (d.chamados_janela / (d.via_m / 1000)).where(urb)
    d["registrado_rank"] = _rank(d.registrado_por_km, urb)
    if ate_mes is None:
        cob = ler("cobertura_localizacao_156_bairro").set_index("bairro_chave").pct_localizada
    else:
        # Cobertura usada na regra de silêncio também deve pertencer à janela de ajuste.
        ocorrencias = ler('ocorrencia_156').reset_index().rename(columns={'index': 'ocorrencia_id'})
        ocorrencias = ocorrencias[ocorrencias.no_recorte & ocorrencias.mes_completo &
                                 (ocorrencias.data_demanda.dt.strftime('%Y-%m') <= ate_mes)].copy()
        achados = set(ler('ocorrencia_156_localizada').ocorrencia_id)
        ocorrencias['localizada'] = ocorrencias.ocorrencia_id.isin(achados)
        cob = 100 * ocorrencias.groupby('bairro_chave').localizada.mean()
    d["cobertura_localizacao_bairro_pct"] = d.bairro_chave.map(cob)
    candidata = (d.esperado_rank >= 0.75) & (d.registrado_rank <= 0.25)
    d["silencio_inconclusivo"] = candidata & ~(d.cobertura_localizacao_bairro_pct >= 85)
    d["zona_silencio"] = candidata & (d.cobertura_localizacao_bairro_pct >= 85)
    d["janela_156"] = f"meses completos até {ate_mes or hist.mes.max()}"
    return d


def zonas_silencio():
    """G-I4 (C5). Sem setor censitário, a população não é repartida por hexágono: a taxa por habitante fica no bairro."""
    d = componentes_motor()
    cols = ["h3", "bairro_codigo", "bairro_nome", "rpa", "regime", "via_m", "c_vulnerabilidade", "esperado_rank", "chamados_janela",
            "registrado_por_km", "registrado_rank", "cobertura_localizacao_bairro_pct", "zona_silencio", "silencio_inconclusivo", "janela_156", "lat", "lon"]
    z = d[cols].copy()
    z["registrado_app"] = 0
    z["metodo"] = "esperado = posto da carência estrutural; registrado = chamados localizados por km de via; zona = esperado ≥ 75% e registrado ≤ 25%"
    z["interpretacao"] = "hipótese de baixa cobertura a investigar; não prova ausência nem presença de lixo"
    z["fonte_dado"] = F156 + "; CIS 2014; faces sem data; Núcleos; listas de descarte"
    g_salvar(z, "motor_esperado_registrado_h3", ["motor_features_h3", "motor_historico_h3_mes"], "período inteiro (25 meses), não trimestral")
    o = ler("ocorrencia_156")
    rec = o[o.no_recorte & o.mes_completo].groupby("bairro_chave").size().rename("chamados_25_meses").reset_index()
    pop = ler("populacao_bairro")[["bairro_codigo", "bairro_chave", "bairro_nome", "rpa", "populacao"]]
    b = pop.merge(rec, on="bairro_chave", how="left").fillna({"chamados_25_meses": 0})
    b["chamados_por_mil_hab"] = 1000 * b.chamados_25_meses / b.populacao
    car = d[d.urbanizado_proxy].groupby("bairro_codigo").agg(carencia_media=("c_vulnerabilidade", "mean"), hex_zona_silencio=("zona_silencio", "sum"),
                                                        hex_urbanos=("h3", "size")).reset_index()
    b = b.merge(car, on="bairro_codigo", how="left")
    b["fonte_dado"] = F156 + "; IBGE Censo 2022 (v0001)"
    g_salvar(b, "silencio_bairro", ["ocorrencia_156", "populacao_bairro", "motor_features_h3"], "população não elimina sozinha o viés de notificação")
    corr = float(b[["chamados_por_mil_hab", "carencia_media"]].rank().corr().iloc[0, 1])
    return {"hex_urbanos": int(d.urbanizado_proxy.sum()), "zonas_silencio": int(z.zona_silencio.sum()), "inconclusivas": int(z.silencio_inconclusivo.sum()),
            "por_regime": z[z.zona_silencio].regime.value_counts().to_dict(), "bairros_com_zona": int((b.hex_zona_silencio > 0).sum()),
            "correlacao_spearman_taxa_por_hab_x_carencia_bairro": round(corr, 2)}


# ====================================================================== 24: onde descartar
def onde_descartar():
    p = ler("ponto_descarte", geo=True).to_crs(4326)
    out = pd.DataFrame(p.drop(columns="geometry"))
    out["lat"] = [g.y if g is not None else None for g in p.geometry]
    out["lon"] = [g.x if g is not None else None for g in p.geometry]
    out["materiais"] = out[MATERIAIS].apply(lambda r: ", ".join(m for m in MATERIAIS if r[m]), axis=1)
    out["fonte_dado"] = out.fontes
    g_salvar(out, "app_onde_descartar", ["ponto_descarte"], "situação inicial histórica; confirmações do app atualizam a situação na aplicação", uso="publico")
    return {"locais": int(len(out)), "com_coordenada": int(out.lat.notna().sum()), "situacoes": out.situacao.value_counts().to_dict(),
            "por_material": {m: int(out[m].sum()) for m in MATERIAIS}}


# ====================================================================== 25: score, teste de partida, fila e rota
def _cfg():
    from .motor import carregar_config
    return carregar_config()


def motor_score():
    """G-I5: componentes e score v0 com o cenário ativo de config/motor_cenarios.json (sem reportes do app)."""
    from .motor import calcular_score
    cfg = _cfg()
    d = componentes_motor()
    comp_cols = ["h3", "bairro_codigo", "bairro_nome", "rpa", "regime", "regime_confianca", "lat", "lon", "x_m", "y_m", "via_m", "urbanizado_proxy",
                 "c_reincidencia", "c_vulnerabilidade", "c_exposicao", "c_contexto", "motivo_reincidencia", "motivo_vulnerabilidade",
                 "motivo_exposicao", "motivo_contexto", "zona_silencio", "silencio_inconclusivo", "meses_com_chamado", "chamados_janela",
                 "coleta_setor", "coleta_frequencia", "coleta_turno", "coleta_dias", "tem_seletiva", "seletiva_frequencia", "seletiva_turno",
                 "polo_300m", "polo_id", "pct_faixa", "descarte_mais_proximo", "dist_descarte_m", "janela_156"]
    comp = d[comp_cols].copy()
    comp["fonte_dado"] = F156 + " + camadas estruturais (anos nas features)"
    g_salvar(comp, "motor_componentes_h3", ["motor_features_h3", "motor_historico_h3_mes"], "entrada do score; a aplicação soma o componente do app em tempo de uso")
    s = calcular_score(comp, cfg)
    s["cenario"] = cfg["cenario_ativo"]
    s["semana_referencia"] = "sem reportes do app; histórico do 156 até mai/2026"
    s["natureza"] = "pontuação heurística provisória; não é previsão avaliada"
    s["motivos"] = s.motivos.map(lambda m: json.dumps(m, ensure_ascii=False))
    g_salvar(s, "motor_score_h3_semana", ["motor_componentes_h3", "config/motor_cenarios.json"])
    pub = s.loc[s.via_m > 0, ["h3", "faixa", "lat", "lon"]].rename(columns={"faixa": "nivel"})
    pub["explicacao"] = "nível de prioridade estimado (heurística provisória); sem contagem de chamados"
    g_salvar(pub, "app_mapa_risco", ["motor_score_h3_semana"], uso="publico")
    return {"cenario": cfg["cenario_ativo"], "limiar": cfg["limiar_fila"], "hexagonos_pontuados": int((s.via_m > 0).sum()),
            "faixas": s[s.via_m > 0].faixa.value_counts().to_dict(), "acima_do_limiar": int(s.em_fila.sum()),
            "acima_do_limiar_por_regime": s[s.em_fila].regime.value_counts().to_dict(),
            "score_medio_por_regime": s[s.via_m > 0].groupby("regime").score.mean().round(1).to_dict()}


def teste_partida(fracao=0.10):
    """Teste de partida (C16), sem vazamento: componentes só com o 156 até ago/2025; alvo = jan–mai/2026.

    Mede a parcela dos chamados localizados de 2026 que cai nos `fracao` hexágonos de maior score e compara
    com (a) o histórico simples por hexágono, (b) o acaso e (c) as ruas (a referência antiga de 35% era de ruas:
    unidade, cobertura e esforço diferentes; por isso a comparação também é feita com o mesmo km de via).
    """
    from .motor import calcular_score
    cfg = _cfg()
    TREINO_FIM, TESTE = "2025-08", ("2026-01", "2026-05")
    hist = g_ler("motor_historico_h3_mes")
    treino = hist[hist.mes <= TREINO_FIM]
    teste = hist[(hist.mes >= TESTE[0]) & (hist.mes <= TESTE[1])]
    assert treino.mes.max() < teste.mes.min(), "vazamento: treino e teste se sobrepõem"
    comp = componentes_motor(ate_mes=TREINO_FIM)
    uni = comp[comp.via_m > 0].copy()
    alvo = teste.groupby("h3").chamados.sum()
    uni["alvo"] = uni.h3.map(alvo).fillna(0.0)
    total = float(uni.alvo.sum())
    k = int(round(fracao * len(uni)))
    linhas = []

    def medir(nome, ordem, unidade="hexágono"):
        top = uni.sort_values(ordem, ascending=False).head(k)
        linhas.append({"criterio": nome, "unidade": unidade, "unidades_no_topo": k, "fracao_das_unidades_pct": round(100 * k / len(uni), 1),
                       "km_de_via_no_topo": round(top.via_m.sum() / 1000, 1), "pct_km_de_via": round(100 * top.via_m.sum() / uni.via_m.sum(), 1),
                       "pct_chamados_2026_capturados": round(100 * top.alvo.sum() / total, 1)})
        return top

    tops = {}
    for cen in cfg["cenarios"]:
        s = calcular_score(uni, cfg, cenario=cen)
        uni["score_" + cen] = s.score.values
        tops[cen] = medir(f"score v0 — cenário {cen}", "score_" + cen)
    uni["hist_simples"] = uni.h3.map(treino.groupby("h3").chamados.sum()).fillna(0.0)
    medir("histórico simples (total de chamados 2024–ago/2025)", "hist_simples")
    linhas.append({"criterio": "acaso (esperado)", "unidade": "hexágono", "unidades_no_topo": k, "fracao_das_unidades_pct": round(100 * fracao, 1),
                   "km_de_via_no_topo": None, "pct_km_de_via": round(100 * fracao, 1), "pct_chamados_2026_capturados": round(100 * fracao, 1)})

    # ruas: recalcula a referência com a deduplicação atual (sem NUMERO), para todas as demandas do recorte
    o = ler("ocorrencia_156")
    o = o[o.no_recorte & o.mes_completo].copy()
    o["mes"] = o.data_demanda.dt.strftime("%Y-%m")
    o["rua"] = o.bairro_chave.fillna("") + "|" + o.logradouro_norm.fillna("")
    tr, te = o[o.mes <= TREINO_FIM], o[(o.mes >= TESTE[0]) & (o.mes <= TESTE[1])]
    cont = tr.groupby("rua").size()
    ruas_univ = pd.Index(sorted(set(tr.rua)))
    kr = int(round(fracao * len(ruas_univ)))
    top_ruas = set(cont.sort_values(ascending=False).head(kr).index)
    pct_ruas = 100 * te.rua.isin(top_ruas).mean()
    ja_tinha = 100 * te.rua.isin(set(tr.rua)).mean()
    rel = ler("territorio_logradouro", geo=True)
    rel["rua"] = rel.bairro_chave.fillna("") + "|" + rel.logradouro_norm.fillna("")
    km_ruas = float(rel[rel.rua.isin(top_ruas) & rel.ligacao_geometrica_valida].comprimento_m.sum() / 1000)
    linhas.append({"criterio": "ruas com mais histórico (todas as demandas, localizadas ou não)", "unidade": "rua (texto + bairro)", "unidades_no_topo": kr,
                   "fracao_das_unidades_pct": round(100 * fracao, 1), "km_de_via_no_topo": round(km_ruas, 1), "pct_km_de_via": None,
                   "pct_chamados_2026_capturados": round(pct_ruas, 1)})
    # mesmo esforço em km: hexágonos pelo score equilibrado até somar o km das ruas do topo
    s = uni.sort_values("score_" + cfg["cenario_ativo"], ascending=False)
    acum = s.via_m.cumsum() / 1000
    mesmo = s[acum <= km_ruas]
    linhas.append({"criterio": f"score v0 ({cfg['cenario_ativo']}) com o mesmo km de via das ruas do topo", "unidade": "hexágono", "unidades_no_topo": int(len(mesmo)),
                   "fracao_das_unidades_pct": round(100 * len(mesmo) / len(uni), 1), "km_de_via_no_topo": round(mesmo.via_m.sum() / 1000, 1),
                   "pct_km_de_via": round(100 * mesmo.via_m.sum() / uni.via_m.sum(), 1), "pct_chamados_2026_capturados": round(100 * mesmo.alvo.sum() / total, 1)})
    t = pd.DataFrame(linhas)
    t['comparabilidade'] = ['mesma unidade H3, cobertura e capacidade de células' if x == 'hexágono' else
                            'referência separada; não comparar diretamente com H3' for x in t.unidade]
    t.loc[t.criterio.str.contains('mesmo km'), 'comparabilidade'] = 'referência exploratória; cobertura de ruas diferente'
    t["treino"], t["teste"] = "jan/2024–ago/2025", "jan–mai/2026"
    t["nota"] = "H3: mesmo universo, alvo localizado e número de células. Ruas: referência separada. Mapas estruturais recebidos em 2026/sem data não permitem backtest estritamente histórico; não comprova previsão."
    g_salvar(t, "motor_teste_partida", ["motor_historico_h3_mes", "ocorrencia_156"], "comparação descritiva; não é validação de previsão")
    # Comparação adicional inteiramente em H3 com o MESMO esforço (km de vias), definido antes do teste.
    # A última célula entra fracionada como orçamento analítico; não representa rota ou atendimento parcial real.
    orcamento_m = float(uni.via_m.sum() * fracao)
    pares = []
    for criterio, coluna in [(f'score v0 — {cen}', 'score_' + cen) for cen in cfg['cenarios']] + [('histórico simples', 'hist_simples')]:
        ordenado = uni.sort_values([coluna, 'h3'], ascending=[False, True]).copy()
        antes = ordenado.via_m.cumsum() - ordenado.via_m
        peso = ((orcamento_m - antes) / ordenado.via_m).clip(0, 1)
        pares.append({'criterio': criterio, 'unidade': 'hexágono', 'orcamento_via_km': orcamento_m / 1000,
                      'via_selecionada_km': float((peso * ordenado.via_m).sum() / 1000),
                      'pct_chamados_2026_capturados': float(100 * (peso * ordenado.alvo).sum() / total),
                      'universo_h3': len(uni), 'alvo_localizado_comum': total,
                      'treino': 'jan/2024–ago/2025', 'teste': 'jan–mai/2026',
                      'limite': 'última célula fracionada só para comparação de esforço; mapas sem versão temporal; não é previsão validada'})
    g_salvar(pd.DataFrame(pares), 'motor_comparacao_esforco', ['motor_historico_h3_mes', 'motor_features_h3'],
             'mesmo universo H3, cobertura, período e orçamento de comprimento de vias')
    cobertura_teste = {'demandas_recorte_teste': len(te), 'demanda_localizada_no_universo_h3': total,
                       'cobertura_pct': 100 * total / len(te), 'treino_fim': TREINO_FIM, 'teste_inicio': TESTE[0],
                       'teste_fim': TESTE[1], 'cobertura_silencio_calculada_so_no_treino': True,
                       'limitacao_temporal_estrutural': 'cadastros recebidos em 2026 e fontes sem data; comparação retrospectiva exploratória, não backtest operacional'}
    json_salvar(GOLD / 'validacao_motor_temporal.json', cobertura_teste)

    # auditoria por quartil de vulnerabilidade (V0 do hexágono): o topo não deve ser só de quem reclama mais
    aud = uni[uni.h3.isin(g_ler("motor_features_h3").dropna(subset=["v0_medio"]).h3)].copy()
    v0 = g_ler("motor_features_h3").set_index("h3").v0_medio
    aud["quartil_v0"] = pd.qcut(aud.h3.map(v0), 4, labels=["Q1 (menor V0)", "Q2", "Q3", "Q4 (maior V0)"]).astype(str)
    top_ids = set(tops[cfg["cenario_ativo"]].h3)
    top_hist = set(uni.sort_values("hist_simples", ascending=False).head(k).h3)
    a = aud.groupby("quartil_v0").agg(hexagonos=("h3", "size"), score_medio=("score_" + cfg["cenario_ativo"], "mean"),
                                      no_topo_score=("h3", lambda x: int(x.isin(top_ids).sum())),
                                      no_topo_historico=("h3", lambda x: int(x.isin(top_hist).sum())),
                                      chamados_2026=("alvo", "sum")).reset_index()
    a["fonte_dado"] = "V0 das faces de quadra (data desconhecida); score do cenário ativo treinado até ago/2025"
    g_salvar(a, "motor_auditoria_quartil", ["motor_features_h3"], "P-M2: distribuição do topo por quartil de V0")
    return {"hexagonos_avaliados": int(len(uni)), "chamados_2026_localizados": round(total, 0), "ja_tinham_chamado_na_rua_pct": round(ja_tinha, 1),
            "tabela": t.drop(columns=["treino", "teste", "nota", "uso"], errors="ignore").to_dict("records"),
            "auditoria_quartil": a.drop(columns=["fonte_dado", "uso"], errors="ignore").round(1).to_dict("records")}


def fila_e_rotas():
    """G-I6: fila do cenário ativo e sequência por setor (vizinho mais próximo, linha reta)."""
    from .motor import fila, sequencia_vizinho_mais_proximo
    cfg = _cfg()
    s = g_ler("motor_score_h3_semana")
    comp_top = s[["contrib_reincidencia", "contrib_vulnerabilidade", "contrib_exposicao", "contrib_contexto"]].idxmax(axis=1)
    s["orgao_sugerido"] = np.select(
        [comp_top.eq("contrib_exposicao") & s.regime.eq("morro"), comp_top.eq("contrib_contexto") & s.polo_300m],
        ["Emlurb + Defesa Civil (encosta)", "Emlurb + Conviva (entorno de feira ou mercado)"], "Emlurb (limpeza urbana)")
    f = fila(s, cfg)
    cols = ["posicao", "h3", "bairro_nome", "rpa", "regime", "score", "faixa", "motivos", "orgao_sugerido", "zona_silencio", "coleta_setor", "lat", "lon", "x_m", "y_m", "cenario"]
    f = f[cols]
    f["tipo_item"] = "hexágono (sem reporte do app)"
    f["fonte_dado"] = F156 + " + camadas estruturais; pesos provisórios"
    g_salvar(f, "painel_fila_priorizada", ["motor_score_h3_semana"], f"limiar {cfg['limiar_fila']}, capacidade {cfg['capacidade_dia']}")
    rotas = []
    for setor, g in f.groupby(f.coleta_setor.fillna('sem setor na fonte')):
        r = sequencia_vizinho_mais_proximo(g)
        r["rota_id"] = f"SETOR-{setor}"
        rotas.append(r)
    rt = pd.concat(rotas, ignore_index=True) if rotas else pd.DataFrame()
    if len(rt):
        rt = rt[["rota_id", "ordem", "h3", "bairro_nome", "score", "dist_reta_m", "lat", "lon"]]
        rt["metodo"] = "vizinho mais próximo a partir do maior score, por setor; distância em linha reta entre centros de hexágono"
        rt["aviso"] = "não é rota viária otimizada nem estimativa de tempo"
        g_salvar(rt, "motor_rotas_sugeridas", ["painel_fila_priorizada"])
    return {"itens_na_fila": int(len(f)), "por_rpa": f.groupby("rpa").size().to_dict(), "por_regime": f.regime.value_counts().to_dict(),
            "rotas": int(rt.rota_id.nunique()) if len(rt) else 0, "soma_linha_reta_km": round(float(rt.dist_reta_m.sum() / 1000), 1) if len(rt) else 0}


# ====================================================================== 26: painéis integrados
def paineis():
    r = {}
    o = ler("ocorrencia_156").reset_index().rename(columns={"index": "ocorrencia_id"})
    rec = o[o.no_recorte & o.mes_completo].copy()
    rec["mes"] = rec.data_demanda.dt.strftime("%Y-%m")
    pop = ler("populacao_bairro")[["bairro_chave", "bairro_nome", "rpa", "populacao"]]

    # pulso do bairro (156 por mil habitantes; a coluna do app nasce vazia e é preenchida pela aplicação)
    pb = rec.groupby(["bairro_chave", "mes"]).size().rename("chamados_156").reset_index().merge(pop, on="bairro_chave")
    pb["chamados_por_mil_hab"] = 1000 * pb.chamados_156 / pb.populacao
    pb["fonte_dado"] = F156 + "; IBGE Censo 2022"
    g_salvar(pb, "painel_pulso_bairro", ["ocorrencia_156", "populacao_bairro"], "chamados registrados, não toneladas")

    # C3 água: só demandas com coordenada (ponto real); esperado = parcela do km de via dentro da faixa
    faixa = ler("faixa_marginal_uniao", geo=True).geometry.iloc[0]
    pts = rec[rec.coord_valida]
    gp = gpd.GeoDataFrame(pts[["ocorrencia_id", "categoria", "rpa"]], geometry=gpd.points_from_xy(pts.lon, pts.lat), crs=4326).to_crs(EPSG_METROS)
    gp = gp[gp.within(_dominio())].copy()
    gp["na_faixa"] = gp.within(faixa)
    ruas = ler("malha_logradouros", geo=True)
    ruas['geometry'] = ruas.intersection(_dominio())
    esperado = float(ruas.intersection(faixa).length.sum() / ruas.length.sum())
    ag = gp.groupby("categoria").agg(demandas_com_coordenada=("na_faixa", "size"), pct_na_faixa=("na_faixa", "mean")).reset_index()
    ag["pct_na_faixa"] *= 100
    ag["pct_km_de_via_na_faixa"] = 100 * esperado
    ag["observado_sobre_esperado"] = ag.pct_na_faixa / ag.pct_km_de_via_na_faixa
    ag["limite"] = f"só demandas com coordenada validada no território ({100 * len(gp) / len(rec):.1f}% do recorte); associação, não causa"
    ag["fonte_dado"] = F156 + "; Faixas marginais"
    g_salvar(ag, "painel_agua_residuo", ["ocorrencia_156", "faixa_marginal", "malha_logradouros"])
    r["agua_observado_sobre_esperado"] = ag.set_index("categoria").observado_sobre_esperado.round(2).to_dict()

    # C10 drenagem: pedidos de drenagem localizados × presença de tubulação no hexágono
    loc = ler("ocorrencia_156_localizada")
    feat = g_ler("motor_features_h3")[["h3", "drenagem_m", "rpa", "coleta_setor", "coleta_frequencia", "coleta_dias"]]
    dr = rec[rec.categoria == "Limpeza de drenagem"][["ocorrencia_id", "situacao"]].merge(loc[["ocorrencia_id", "h3", "peso"]], on="ocorrencia_id").merge(feat, on="h3")
    dr["sem_rede"] = dr.drenagem_m == 0
    dr["aberto"] = dr.situacao != "ATENDIDA"
    pdn = dr.groupby("rpa").apply(lambda t: pd.Series({
        "pedidos_localizados": t.peso.sum(), "pct_em_hex_sem_tubulacao": 100 * t.peso[t.sem_rede].sum() / t.peso.sum(),
        "pct_nao_atendido": 100 * t.peso[t.aberto].sum() / t.peso.sum()})).reset_index()
    pdn["fonte_dado"] = F156 + "; Núcleos — drenagem (sem data)"
    pdn["limite"] = "a camada de drenagem pode não ter toda a rede; ausência na camada não prova ausência de galeria"
    g_salvar(pdn, "painel_drenagem", ["ocorrencia_156_localizada", "rede_drenagem"])
    r["drenagem_pct_sem_tubulacao_cidade"] = round(float(100 * dr.peso[dr.sem_rede].sum() / dr.peso.sum()), 1)

    # C9 plano × demanda: por setor regular; nos alternados, parcela aberta em dia sem coleta (esperado: 4 de 7 dias)
    lx = rec[rec.categoria == "Lixo e descarte"][["ocorrencia_id", "data_demanda"]].merge(loc[["ocorrencia_id", "h3", "peso"]], on="ocorrencia_id").merge(feat, on="h3")
    lx = lx[lx.coleta_setor.notna()]
    lx["dia_sem_coleta"] = [str(d.weekday()) not in str(c).split(",") for d, c in zip(lx.data_demanda, lx.coleta_dias)]
    pv = lx.groupby(["coleta_setor", "coleta_frequencia"]).apply(lambda t: pd.Series({
        "chamados_lixo_localizados": t.peso.sum(), "pct_abertos_em_dia_sem_coleta": 100 * t.peso[t.dia_sem_coleta].sum() / t.peso.sum()})).reset_index()
    pv["pct_dias_sem_coleta_no_calendario"] = pv.coleta_frequencia.map(lambda f: round(100 * (1 / 7 if f == "DIARIA" else 4 / 7), 1))
    pv["limite"] = "data de abertura do chamado, não do descarte; 'DIARIA' lida como segunda a sábado (a confirmar)"
    pv["fonte_dado"] = F156 + "; setores de coleta (vigência a confirmar)"
    g_salvar(pv, "painel_plano_vs_demanda", ["ocorrencia_156_localizada", "setor_coleta"])
    alt = lx[lx.coleta_frequencia != "DIARIA"]
    r["plano_alternados_pct_em_dia_sem_coleta"] = round(float(100 * alt.peso[alt.dia_sem_coleta].sum() / alt.peso.sum()), 1)

    # C4 encosta: Defesa Civil (2023–24) e 156 lado a lado por bairro × mês (só 2024 se sobrepõe)
    enc = g_ler("risco_encosta_bairro_mes").drop(columns=["uso", "fonte_dado"])
    pe = enc.merge(rec.groupby(["bairro_chave", "mes"]).size().rename("chamados_156").reset_index(), on=["bairro_chave", "mes"], how="left")
    pe["fonte_dado"] = "Defesa Civil 2023–24 (agregado) e " + F156
    g_salvar(pe, "painel_encosta", ["risco_encosta_bairro_mes", "ocorrencia_156"], "coincidência por bairro é pista, não causa")

    # C11 polos: demandas com coordenada a até 300 m, em dia com e sem feira (só polos com dias conhecidos)
    polos = ler("polo_comercio", geo=True)
    pk = polos[polos.dias_conhecidos & ~polos.coordenada_suspeita]
    j = gpd.sjoin_nearest(gp.merge(rec[["ocorrencia_id", "data_demanda"]], on="ocorrencia_id"), pk[["polo_id", "nome"] + _SEM + ["geometry"]], max_distance=300, distance_col="d")
    j = j[~j.ocorrencia_id.duplicated()]
    j["dia_de_feira"] = [bool(row[_SEM[d.weekday()]]) for d, (_, row) in zip(j.data_demanda, j.iterrows())]
    pp = j.groupby(["polo_id", "nome"]).agg(demandas_300m=("d", "size"), em_dia_de_feira=("dia_de_feira", "sum")).reset_index()
    dias_abertos = pk.set_index("nome")[_SEM].fillna(False)
    calendario = pd.date_range('2024-01-01', '2026-05-31')
    calendario = calendario[(calendario <= '2025-08-31') | (calendario >= '2026-01-01')]
    pp['dias_de_feira_por_semana'] = pp.nome.map(dias_abertos.sum(axis=1))
    pp['dias_com_feira_observados'] = pp.nome.map(lambda n: sum(bool(dias_abertos.loc[n, _SEM[d.weekday()]]) for d in calendario))
    pp['dias_sem_feira_observados'] = len(calendario) - pp.dias_com_feira_observados
    pp['por_dia_com_feira'] = pp.em_dia_de_feira / pp.dias_com_feira_observados.replace(0, np.nan)
    pp['por_dia_sem_feira'] = (pp.demandas_300m - pp.em_dia_de_feira) / pp.dias_sem_feira_observados.replace(0, np.nan)
    pp["exibicao_demandas"] = menos_de_5(pp.demandas_300m)
    pp["limite"] = "só demandas com coordenada; amostras pequenas; hipótese a testar, não efeito comprovado"
    pp["fonte_dado"] = F156 + "; CONVIVA"
    g_salvar(pp, "painel_polos_comercio", ["ocorrencia_156", "polo_comercio"])

    # C7/C8 rede de descarte: hexágonos longe de quem recebe entulho e com entulho registrado (candidatos a investigar)
    hist = g_ler("motor_historico_h3_mes")
    f = g_ler("motor_features_h3")
    ent = rec[rec.servico_descricao.str.contains("ENTULHO|METRALHA", na=False)][["ocorrencia_id"]].merge(loc[["ocorrencia_id", "h3", "peso"]], on="ocorrencia_id")
    eh = ent.groupby("h3").peso.sum().rename("entulho_156")
    cand = f[["h3", "bairro_nome", "rpa", "dist_entulho_m", "tem_seletiva", "lat", "lon", "via_m"]].merge(eh, on="h3", how="left").fillna({"entulho_156": 0})
    cand["deserto_descarte"] = (cand.dist_entulho_m > 2000) & ~cand.tem_seletiva & (cand.via_m >= 200)
    cand["candidato"] = cand.deserto_descarte & (cand.entulho_156 >= cand.entulho_156[cand.via_m >= 200].quantile(0.75))
    cand["fonte_dado"] = F156 + "; listas históricas de descarte (rede atual não confirmada)"
    cand["limite"] = "distância em linha reta a pontos de listas antigas; 7 ecoestações sem coordenada ficam fora do cálculo"
    g_salvar(cand, "painel_rede_descarte", ["motor_features_h3", "ocorrencia_156_localizada", "ponto_descarte"],
             "efeito de Macaxeira e Cais de Santa Rita (C7) NÃO calculado: unidades sem coordenada conferida")
    r.update(desertos_de_descarte=int(cand.deserto_descarte.sum()), candidatos=int(cand.candidato.sum()), polos_avaliados=int(len(pp)),
             c7_efeito_ecoestacao="pendente: coordenadas das unidades novas não conferidas")
    json_salvar(GOLD / "validacao_paineis.json", r)
    return r


# ====================================================================== 27: tabelas do app
CATEGORIAS = ["entulho", "lixo doméstico", "volumoso", "poda", "recicláveis", "ponto de descarte cheio",
              "canaleta ou galeria entupida", "lixo na beira de rio ou canal"]
VOLUMES = {"saco": "cabe em sacos", "carrinho": "um carrinho de mão", "cacamba": "uma caçamba ou mais"}


def tabelas_app():
    """G-I7: contexto por hexágono, missões e exemplos simulados (marcados) para a demonstração local."""
    c = g_ler("motor_componentes_h3")
    s = g_ler("motor_score_h3_semana")[["h3", "score", "faixa"]]
    nivel = pd.cut(c.c_reincidencia, [-1, 0.0001, 0.5, 0.8, 1.01], labels=["sem histórico localizado", "recorrência baixa", "recorrência média", "recorrência alta"]).astype(str)
    ctx = pd.DataFrame({
        "h3": c.h3, "bairro_codigo": c.bairro_codigo, "bairro_nome": c.bairro_nome, "rpa": c.rpa, "lat": c.lat, "lon": c.lon,
        "coleta_frequencia": c.coleta_frequencia, "coleta_turno": c.coleta_turno, "coleta_dias": c.coleta_dias,
        "seletiva_dia": c.seletiva_frequencia, "seletiva_turno": c.seletiva_turno,
        "nivel_reincidencia": nivel.where(c.chamados_janela >= 5, "poucos registros"),
        "beira_de_rio": c.pct_faixa > 0.05, "polo_proximo": c.polo_id, "zona_silencio": c.zona_silencio,
        "descarte_mais_proximo": c.descarte_mais_proximo, "dist_descarte_m": c.dist_descarte_m.round(-1), "regime": c.regime})
    ctx["nota_coleta"] = "setores: vigência a confirmar; frequência DIARIA interpretada como segunda a sábado, hipótese a confirmar com a Emlurb"
    ctx["nota_reincidencia"] = "nível, não contagem; histórico do 156 2024–2026 (endereços distintos não verificáveis)"
    ctx["fonte_dado"] = "Núcleos (setores), Faixas, listas de descarte, CONVIVA, 156 2024–2026"
    g_salvar(ctx, "app_contexto_local", ["motor_componentes_h3"], uso="publico")

    z = c[c.zona_silencio].merge(s, on="h3").sort_values("c_vulnerabilidade", ascending=False).head(40)
    m = [{"missao_id": f"MS-{h}", "tipo": "zona_silencio", "h3": h, "bairro_nome": b, "titulo": "Ajude a mapear esta área",
          "descricao": "Há pouco registro aqui para a carência do lugar. Reporte ou confirme um alerta nesta área.",
          "pontos": 20, "lat": la, "lon": lo} for h, b, la, lo in zip(z.h3, z.bairro_nome, z.lat, z.lon)]
    m += [{"missao_id": "MS-DESCARTE-3", "tipo": "confirmar_descarte", "h3": None, "bairro_nome": None, "titulo": "Confirme 3 pontos de descarte",
           "descricao": "As listas oficiais são antigas. Diga se 3 pontos funcionam, fecharam ou mudaram.", "pontos": 15, "lat": None, "lon": None},
          {"missao_id": "MS-ALERTA-SEM-CONF", "tipo": "alerta_sem_confirmacao", "h3": None, "bairro_nome": None, "titulo": "Confira um alerta sem confirmação",
           "descricao": "Responda 'ainda está aí?' num alerta que ninguém confirmou.", "pontos": 10, "lat": None, "lon": None}]
    mi = pd.DataFrame(m)
    mi["regra"] = "provisória (hipótese de teste, 11/11/2026); cada missão pontua uma vez por pessoa"
    mi["fonte_dado"] = "motor_esperado_registrado_h3 (hipótese de baixa cobertura)"
    g_salvar(mi, "app_missoes", ["motor_componentes_h3"], uso="publico")

    # exemplos simulados: posições no centro de hexágonos (não são ocorrências reais)
    rng = np.random.default_rng(20261111)
    top = c.merge(s, on="h3")
    escolhidos = pd.concat([top[top.regime == "morro"].nlargest(4, "score"), top[top.regime == "planicie"].nlargest(4, "score"),
                            top[top.zona_silencio].nlargest(2, "c_vulnerabilidade"), top[top.polo_300m].nlargest(2, "score")]).drop_duplicates("h3")
    cats = {"morro": ["entulho", "canaleta ou galeria entupida", "lixo doméstico", "volumoso"],
            "planicie": ["lixo na beira de rio ou canal", "entulho", "volumoso", "lixo doméstico"], "indefinido": ["entulho", "lixo doméstico"]}
    ex = []
    for i, (_, row) in enumerate(escolhidos.iterrows()):
        opc = cats[row.regime]
        ex.append({"exemplo_id": f"EX-{i + 1:02d}", "h3": row.h3, "lat": row.lat, "lon": row.lon, "bairro_nome": row.bairro_nome,
                   "categoria": opc[i % len(opc)], "volume": ["saco", "carrinho", "cacamba"][int(rng.integers(0, 3))],
                   "confirmacoes_simuladas": int(rng.integers(0, 3)), "texto": "Exemplo simulado para demonstração (não é ocorrência real)."})
    ex = pd.DataFrame(ex)
    ex["origem"] = "simulado"
    g_salvar(ex, "app_exemplos_simulados", ["motor_score_h3_semana"], "EXEMPLOS: nunca contar como dado real", uso="publico")
    return {"contexto_hexagonos": int(len(ctx)), "missoes": int(len(mi)), "missoes_zona_silencio": int((mi.tipo == "zona_silencio").sum()),
            "exemplos_simulados": int(len(ex)), "niveis_reincidencia": ctx.nivel_reincidencia.value_counts().to_dict()}
