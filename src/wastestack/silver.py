"""Entidades Silver além do território: uma função por assunto, lendo só o Bronze.

Cada função grava Parquet/GeoParquet em data/silver/ (WGS84 no arquivo, medidas em EPSG:31985)
e devolve um resumo com as contagens usadas na conferência. Fontes antigas levam o ano no próprio dado.
"""
import re

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely import union_all

from .arquivos import codigo, csv_ler, fonte, geo_ler, json_salvar, ler, numero, salvar
from .config import BRONZE, EPSG_METROS, SILVER
from .limpeza import chave_bairro, normalizar_nome, padrao_logradouro

RESTRITO_SILVER = SILVER / "_restrito"
COD_RECIFE = "2611606"


def _pontos(frame, lat, lon):
    g = gpd.GeoDataFrame(frame, geometry=gpd.points_from_xy(numero(frame[lon]), numero(frame[lat])), crs=4326)
    return g.to_crs(EPSG_METROS)


def _dominio():
    """União IBGE 2025 + bairros (política de borda da grade): nada é descartado por estar numa só fonte."""
    ibge = gpd.read_parquet(SILVER / "contorno_municipal_ibge_2025.parquet").to_crs(EPSG_METROS).geometry.iloc[0]
    bairros = gpd.read_parquet(SILVER / "contorno_uniao_bairros.parquet").to_crs(EPSG_METROS).geometry.iloc[0]
    return ibge.union(bairros)


def _agrupar_por_distancia(pontos, metros):
    """Rótulo de grupo para pontos ligados em cadeia a até `metros` (componentes conexos)."""
    uniao = union_all(pontos.geometry.buffer(metros / 2).array)
    partes = gpd.GeoDataFrame(geometry=list(getattr(uniao, "geoms", [uniao])), crs=pontos.crs)
    partes["grupo"] = range(len(partes))
    return gpd.sjoin(pontos[["geometry"]], partes, predicate="within")["grupo"].reindex(pontos.index)


# ---------------------------------------------------------------- 11 população
def preparar_populacao():
    from .validacao import conferir_dicionario
    conferir_dicionario()
    """Censo 2022 por bairro. v0001 = "Total de pessoas" (conferido no dicionário oficial em docs/dicionarios/ibge/)."""
    bruto = csv_ler(fonte("Agregados_por_bairros_basico_BR_20260520.zip"))
    rec = bruto[bruto.CD_MUN == COD_RECIFE].copy()
    rec["bairro_chave"] = rec.NM_BAIRRO.map(chave_bairro)
    bairros = ler("territorio_bairro", geo=True)[["bairro_codigo", "bairro_chave", "bairro_nome", "rpa", "area_km2"]]
    junto = pd.DataFrame(bairros).merge(rec, on="bairro_chave", how="outer", indicator=True)
    sem_ligacao = junto[junto._merge != "both"]
    ok = junto[junto._merge == "both"].copy()
    saida = pd.DataFrame({
        "bairro_codigo": ok.bairro_codigo, "bairro_chave": ok.bairro_chave, "bairro_nome": ok.bairro_nome, "rpa": ok.rpa,
        "cd_bairro_ibge": ok.CD_BAIRRO, "nome_ibge": ok.NM_BAIRRO,
        "populacao": numero(ok.v0001).astype("Int64"), "domicilios": numero(ok.v0002).astype("Int64"),
        "domicilios_particulares_ocupados": numero(ok.v0007).astype("Int64"),
        "area_km2_prefeitura": ok.area_km2, "area_km2_ibge": numero(ok.AREA_KM2),
    })
    saida["hab_por_km2"] = saida.populacao / saida.area_km2_prefeitura
    saida["fonte"] = "IBGE, Censo 2022, Agregados por Bairro (v0001 = Total de pessoas)"
    saida["ano"] = 2022
    assert saida.bairro_codigo.is_unique
    salvar(saida, "populacao_bairro", fontes=["IBGE Censo 2022 agregados por bairro"],
           nota="População por bairro; não repartida por hexágono (sem setor censitário).")
    resumo = {"bairros_ibge_recife": int(len(rec)), "bairros_casados": int(len(saida)),
              "sem_ligacao": sem_ligacao[["bairro_chave", "_merge"]].astype(str).to_dict("records"),
              "populacao_total": int(saida.populacao.sum()), "populacao_ibge_recife": int(numero(rec.v0001).sum()),
              "populacao_sem_ligacao": int(numero(rec.v0001).sum() - saida.populacao.sum()),
              "variavel": "v0001 = Total de pessoas (dicionário IBGE de 20/05/2026, aba Dicionário Básico)"}
    json_salvar(SILVER / "validacao_populacao.json", resumo)
    return resumo


# ---------------------------------------------------------------- 13 faixas marginais
def preparar_faixas():
    f = geo_ler("faixas-marginais-dos-recursos-hidricos.geojson")
    invalidas = int((~f.is_valid).sum())
    f["geometry"] = shapely.make_valid(f.geometry.array)
    f = f.rename(columns={"objectid": "faixa_id"})[["faixa_id", "nome", "bacia", "geometry"]]
    f["nome"] = f.nome.fillna("").str.strip()
    f["bacia"] = f.bacia.fillna("").str.strip().str.upper()
    f["area_m2"] = f.area
    partes = f.geometry.map(lambda g: list(g.geoms) if hasattr(g, 'geoms') else [g])
    buracos = partes.map(lambda ps: sum(len(p.interiors) for p in ps))
    largura = 2 * f.area / f.length
    anel = partes.map(len).eq(1) & buracos.gt(0) & f.area.lt(60000) & largura.between(45, 52)
    fragmento = f.area.lt(5000) & buracos.eq(0)
    f["classe"] = np.select([anel, fragmento], ["anel", "fragmento"], "faixa")
    f["classe_metodo"] = "receita dataset 2: partes, buracos, área e largura aparente; classe geométrica, não classificação legal"
    f["fonte"] = "Prefeitura do Recife, Faixas marginais dos recursos hídricos; base legal citada de 2003 e 2008"
    simples = f.copy()
    simples["geometry"] = simples.geometry.simplify(1)
    dif = float(simples.area.sum() / f.area.sum() - 1)
    salvar(f, "faixa_marginal", fontes=["Faixas marginais (recebido em 11/11/2026)"])
    salvar(simples, "faixa_marginal_simplificada", fontes=["Faixas marginais"], nota="simplificada a 1 m para o mapa")
    uniao = gpd.GeoDataFrame({"fonte": ["união das faixas marginais"]}, geometry=[union_all(f.geometry.array)], crs=EPSG_METROS)
    salvar(uniao, "faixa_marginal_uniao", fontes=["Faixas marginais"])
    bairros = union_all(ler("territorio_bairro", geo=True).geometry.array)
    resumo = {"feicoes": len(f), "invalidas_corrigidas": invalidas, "classes": f.classe.value_counts().to_dict(),
              "area_faixas_km2": float(uniao.area.iloc[0] / 1e6),
              "pct_area_bairros_em_faixa": float(100 * uniao.geometry.iloc[0].intersection(bairros).area / bairros.area),
              "variacao_area_simplificacao_pct": 100 * dif}
    json_salvar(SILVER / "validacao_faixas.json", resumo)
    return resumo


# ---------------------------------------------------------------- 14 pontos de descarte
MATERIAIS = ["entulho", "volumosos", "poda", "reciclaveis", "eletronicos", "oleo", "pilhas", "pneus", "lampadas",
             "medicamentos", "organico"]
_PALAVRAS = {
    "entulho": r"CONSTRUCAO|METRALHA|ENTULHO", "volumosos": r"MOVEIS|VOLUMOSO|ELETRODOMESTICO",
    "poda": r"PODA", "reciclaveis": r"RECICLAVE|METAL|PAPEL|PLASTICO|PASTICO|VIDRO|ISOPOR",
    "eletronicos": r"ELETRO?N|ELETROELETR", "oleo": r"OLEO", "pilhas": r"PILHA|BATERIA", "pneus": r"PNEU",
    "lampadas": r"LAMPADA", "medicamentos": r"MEDICAMENTO", "organico": r"ORGANICO",
}


def _tipo_local(texto):
    n = normalizar_nome(texto) or ""
    if "ECOESTACAO" in n.replace(" ", ""):
        return "ecoestação"
    for chave, tipo in (("PEV", "PEV"), ("ECOPONTO", "ecoponto"), ("COOPERATIVA", "cooperativa")):
        if chave in n:
            return tipo
    return "parceiro ou outro" if n else "não informado"


def _materiais(texto):
    n = normalizar_nome(texto) or ""
    return {m: bool(re.search(p, n)) for m, p in _PALAVRAS.items()}


def preparar_descarte():
    """Tabela única de locais de descarte (datasets 3, 4, 5 e lista de apoio), juntando registros a até 50 m.

    Nenhuma linha confirma funcionamento atual: a situação nasce "histórico, a confirmar" e a data de
    verificação fica desconhecida até uma confirmação (vistoria ou evento do app).
    """
    partes = []
    p3 = csv_ler(fonte("pontos-de-coleta.csv"))
    p3 = p3.assign(fonte="Pontos de coleta seletiva (dataset 3; sem data na fonte)", nome=p3.observacao,
                   tipo=p3.observacao.map(_tipo_local), endereco=p3.endereco + " " + p3.complemento,
                   material_texto=p3.tiporesiduo, origem_material="declarado na fonte (tiporesiduo)")
    partes.append(p3)
    p4 = csv_ler(fonte("onde-descartar.csv")).drop_duplicates()
    # As colunas SIM/NÃO da fonte estão inconsistentes (análise do dataset 4): usa-se só o texto de tiporesiduo.
    p4 = p4.assign(fonte="Destinação de resíduos sólidos (dataset 4; atualização semestral declarada)",
                   tipo=p4.nome.map(_tipo_local), endereco=p4.endereco + " " + p4.complemento,
                   nome=p4.observacao, material_texto=p4.tiporesiduo, origem_material="declarado na fonte (tiporesiduo)")
    partes.append(p4)
    p5 = csv_ler(fonte("eco-estacoes-2023.csv")).rename(columns={"lat": "latitude", "long": "longitude", "descricao": "nome"})
    p5 = p5.assign(fonte="Pequenos volumes — Eco Estações 2023 (dataset 5; rede de 2017 a meados de 2024)", tipo="ecoestação",
                   material_texto="entulho, moveis volumosos, poda, reciclaveis",
                   origem_material="regra geral das ecoestações (documento do projeto); não consta do arquivo")
    partes.append(p5)
    cols = ["fonte", "nome", "tipo", "bairro", "endereco", "material_texto", "origem_material", "latitude", "longitude"]
    linhas = pd.concat([p[cols] for p in partes], ignore_index=True)
    linhas["origem_coordenada"] = "coordenada do arquivo da fonte"
    pts = _pontos(linhas, "latitude", "longitude")
    dominio = _dominio()
    pts["no_recife"] = pts.within(dominio)
    fora = int((~pts.no_recife).sum())
    pts = pts[pts.no_recife].copy()
    pts["grupo"] = _agrupar_por_distancia(pts, 50)
    mats = pd.DataFrame([_materiais(t) for t in pts.material_texto], index=pts.index)
    pts = pts.join(mats)
    ordem = {"ecoestação": 0, "cooperativa": 1, "PEV": 2, "ecoponto": 3, "parceiro ou outro": 4, "não informado": 5}
    locais = []
    for grupo, g in pts.groupby("grupo"):
        g = g.sort_values("tipo", key=lambda s: s.map(ordem))
        nomes = [n for n in g.nome if n and normalizar_nome(n) not in ("PEV", "ECOPONTO", "COOPERATIVA", "ECOESTACAO")]
        centro = g.geometry.union_all().centroid
        locais.append({"tipo": g.tipo.iloc[0], "nome": (nomes[0] if nomes else g.tipo.iloc[0]).strip(),
                       "endereco_fonte": next((e.strip() for e in g.endereco if e and e.strip()), ""),
                       "bairro_fonte": next((b.strip() for b in g.bairro if b and b.strip()), ""),
                       **{m: bool(g[m].any()) for m in MATERIAIS},
                       "origem_material": "; ".join(sorted(set(g.origem_material))),
                       "fontes": " | ".join(sorted(set(g.fonte))), "n_linhas_fonte": len(g), "n_fontes": g.fonte.nunique(),
                       "origem_coordenada": "coordenada do arquivo da fonte (centro dos registros a até 50 m)",
                       "localizacao": "informada na fonte", "geometry": centro})
    locais = gpd.GeoDataFrame(locais, crs=EPSG_METROS)

    # Lista de apoio das 17 ecoestações: completa datas de abertura e acrescenta as unidades sem coordenada.
    apoio = csv_ler(fonte("lista-apoio-ecoestacoes.csv"))
    eco = locais[locais.tipo == "ecoestação"]
    p5pts = _pontos(p5, "latitude", "longitude")
    locais["data_abertura"] = ""
    locais["abertura_fonte"] = ""
    locais["divergencia_registrada"] = ""
    novas = []
    for _, a in apoio.iterrows():
        if a.no_arquivo_2023 == "sim":
            alvo = p5pts[p5pts.item == a.item_arquivo]
            i = eco.distance(alvo.geometry.iloc[0]).idxmin()
            locais.loc[i, ["data_abertura", "abertura_fonte", "divergencia_registrada"]] = [a.abertura, a.abertura_fonte, a.divergencia]
            if "Ecoesta" not in locais.at[i, "nome"]:
                locais.at[i, "nome"] = "Ecoestação " + a.unidade
        else:
            novas.append({"tipo": "ecoestação", "nome": "Ecoestação " + a.unidade, "endereco_fonte": a.endereco,
                          "bairro_fonte": a.bairro, **{m: m in ("entulho", "volumosos", "poda", "reciclaveis") for m in MATERIAIS},
                          "origem_material": "regra geral das ecoestações (documento do projeto); não conferida por unidade",
                          "fontes": "Lista de apoio das 17 ecoestações (pasta 10, dataset 5): " + a.endereco_fonte,
                          "n_linhas_fonte": 1, "n_fontes": 1, "origem_coordenada": "ausente: unidade sem coordenada conferida",
                          "localizacao": "pendente", "geometry": None, "data_abertura": a.abertura,
                          "abertura_fonte": a.abertura_fonte, "divergencia_registrada": a.divergencia})
    locais = pd.concat([locais, gpd.GeoDataFrame(novas, geometry="geometry", crs=EPSG_METROS)], ignore_index=True)
    locais = gpd.GeoDataFrame(locais, geometry="geometry", crs=EPSG_METROS)
    bairros = ler("territorio_bairro", geo=True)
    com = locais[locais.geometry.notna()]
    lig = gpd.sjoin(com[["geometry"]], bairros[["bairro_codigo", "bairro_nome", "rpa", "geometry"]], how="left", predicate="within")
    lig = lig[~lig.index.duplicated()]
    for c in ("bairro_codigo", "bairro_nome", "rpa"):
        locais[c] = lig[c].reindex(locais.index)
    locais["ponto_id"] = ["PD%04d" % (i + 1) for i in range(len(locais))]
    locais["situacao"] = "histórico, a confirmar"
    locais["data_verificacao"] = "desconhecida"
    locais["observacao_situacao"] = "A presença numa lista pública não confirma funcionamento atual nem verifica a organização."
    eco_regra = "Regra geral divulgada para ecoestações (até 1 m³ por dia, segunda a sábado, 8h–16h): a confirmar na fonte oficial"
    locais["regra_uso"] = np.where(locais.tipo == "ecoestação", eco_regra, "não informada na fonte")
    locais["horario"] = "não informado na fonte"
    salvar(locais, "ponto_descarte", fontes=["datasets 3, 4 e 5 + lista de apoio"],
           nota="situação e verificação começam desconhecidas; materiais vêm do texto da fonte")
    resumo = {"linhas_fonte": int(len(linhas)), "linhas_fora_do_recife": fora, "locais": int(len(locais)),
              "locais_com_coordenada": int(locais.geometry.notna().sum()), "por_tipo": locais.tipo.value_counts().to_dict(),
              "ecoestacoes": int((locais.tipo == "ecoestação").sum()),
              "ecoestacoes_localizacao_pendente": int(((locais.tipo == "ecoestação") & locais.geometry.isna()).sum()),
              "bairros_sem_local": int(len(bairros) - locais.bairro_codigo.nunique()),
              "locais_que_recebem_entulho": int(locais.entulho.sum())}
    json_salvar(SILVER / "validacao_descarte.json", resumo)
    return resumo


# ---------------------------------------------------------------- 15 Defesa Civil (restrito)
_SOCIAL = r"SOCIAL|PORTA|CESTA|KIT|COLCH|LENCO|COBERTOR|FAMILIA|PSICOLOG|CADASTRO|EDUCATIV|PARCEIRO|ORIENTAD"
_ZELADORIA = r"ROCAGEM|CAPINA|ENTULHO|VEGETACAO|CORTE DE|CANALETA|BANANEIRA"


def _grupo_dc(sol, oc, acao):
    if "ALAGA" in oc or "ALAGAD" in sol:
        return "alagamento"
    if "DESLIZAMENTO" in oc:
        return "deslizamento"
    if "LONA" in sol or "LONA" in acao:
        return "lona"
    if re.search(_ZELADORIA, acao) or re.search(_ZELADORIA, sol):
        return "zeladoria"
    if re.search(_SOCIAL, acao):
        return "social"
    if "VISTORIA" in sol or "ANALISE" in acao or "MAPEAMENTO" in acao:
        return "vistoria"
    if "MONITORAMENTO" in sol:
        return "monitoramento"
    return "outro"


def preparar_defesa_civil():
    """Ações da Defesa Civil 2023–2024. Saída RESTRITA (rua e dia): só agregados saem para Gold e telas."""
    pasta = BRONZE / "_restrito" / "defesa_civil" / "2026-11-11"
    partes = [csv_ler(pasta / f"atendimentos-{a}.csv").assign(arquivo_ano=a) for a in (2023, 2024)]
    dc = pd.concat(partes, ignore_index=True)
    dc = dc.apply(lambda s: s.str.replace(" ", " ").str.strip() if s.dtype == object else s)
    out = pd.DataFrame({"arquivo_ano": dc.arquivo_ano})
    out["data"] = pd.to_datetime(dc.Data, errors="coerce")
    out["regional"] = dc.Regional.str.title()
    out["regional_morro"] = ~out.regional.eq("Planície")
    out["bairro_chave"] = dc.Bairro.map(chave_bairro)
    # O trecho depois da vírgula é um código mascarado do imóvel: descartado aqui.
    out["logradouro_norm"] = dc.Endereco.str.rsplit(",", n=1).str[0].map(padrao_logradouro)
    out["localidade"] = dc.Localidade.map(normalizar_nome)
    grau = dc.Grau_de_Risco.str.upper()
    out["grau_risco"] = grau.where(grau.isin(["R1", "R2", "R3", "R4"]))
    sol, oc, acao = (dc[c].map(lambda t: normalizar_nome(t) or "") for c in ("Solicitacao", "Ocorrencia", "Tipo_da_Acao"))
    out["grupo"] = [_grupo_dc(s, o, a) for s, o, a in zip(sol, oc, acao)]
    out["ocorrencia"] = oc
    out["tipo_acao"] = acao
    # A medida consta também em ações cuja solicitação não foi rotulada como lona.
    out["area_lona_m2"] = numero(dc.Area_m).where(lambda s: s > 0)
    bairros = set(ler("territorio_bairro", geo=True).bairro_chave)
    out["bairro_casado"] = out.bairro_chave.isin(bairros)
    RESTRITO_SILVER.mkdir(parents=True, exist_ok=True)
    salvar(out, "acao_defesa_civil", camada=RESTRITO_SILVER, fontes=["Defesa Civil 2023–2024 (restrito)"],
           nota="RESTRITO: rua × dia. Ações sociais nunca saem por rua × dia; publicar somente agregados.")
    v = out[out.data.dt.year.isin([2023, 2024])]
    resumo = {"registros": int(len(out)), "registros_2023_2024": int(len(v)), "data_min": str(out.data.min().date()),
              "data_max": str(out.data.max().date()), "pct_bairro_casado": float(100 * out.bairro_casado.mean()),
              "pct_regional_morro": float(100 * out.regional_morro.mean()), "por_grupo": out.grupo.value_counts().to_dict(),
              "alagamento_registros": int(out.grupo.eq("alagamento").sum()),
              "dias_com_alagamento": int(out[out.grupo.eq("alagamento")].data.dt.date.nunique()),
              "area_lona_m2": float(out.area_lona_m2.sum()), "pct_grau_preenchido": float(100 * out.grau_risco.notna().mean()),
              "ruas_r4": int(out[out.grau_risco.eq("R4")].groupby(["bairro_chave", "logradouro_norm"]).ngroups),
              "arquivo_2025": "1 registro (data de dez/2024): não representa cobertura de 2025"}
    json_salvar(SILVER / "validacao_defesa_civil.json", resumo)
    return resumo


# ---------------------------------------------------------------- 16 setores, drenagem, esgoto
_DIAS = {"SEGUNDA-FEIRA": 0, "TERÇA-FEIRA": 1, "QUARTA-FEIRA": 2, "QUINTA-FEIRA": 3, "SEXTA-FEIRA": 4, "SÁBADO": 5}
_FREQ = {"DIARIA": [0, 1, 2, 3, 4, 5], "SEG-QUA-SEX": [0, 2, 4], "TER-QUI-SAB": [1, 3, 5]}


def preparar_nucleos():
    s = geo_ler("setores-de-coleta.geojson")
    invalidos = int((~s.is_valid).sum())
    s["geometry"] = shapely.make_valid(s.geometry.array)
    for c in ("SETOR_REGU", "FREQ_REGUL", "TURNO_REGU", "SETOR_SELE", "FREQ_SELET", "TURNO_SELE"):
        s[c] = s[c].fillna("").astype(str).str.strip()
    reg = s[s.SETOR_REGU != ""].dissolve(by="SETOR_REGU", aggfunc="first").reset_index()
    reg = reg.rename(columns={"SETOR_REGU": "setor", "FREQ_REGUL": "frequencia", "TURNO_REGU": "turno"})[["setor", "frequencia", "turno", "geometry"]]
    reg["tipo"] = "regular"
    # "DIARIA" lida como segunda a sábado: hipótese a confirmar com a Emlurb (o domingo não aparece na fonte).
    reg["dias_coleta"] = reg.frequencia.map(lambda f: ",".join(map(str, _FREQ.get(f, []))))
    sel = s[s.SETOR_SELE != ""].dissolve(by="SETOR_SELE", aggfunc="first").reset_index()
    sel = sel.rename(columns={"SETOR_SELE": "setor", "FREQ_SELET": "frequencia", "TURNO_SELE": "turno"})[["setor", "frequencia", "turno", "geometry"]]
    sel["tipo"] = "seletiva"
    sel["dias_coleta"] = sel.frequencia.map(lambda f: str(_DIAS.get(f, "")))
    setores = pd.concat([reg, sel], ignore_index=True)
    setores["area_km2"] = setores.area / 1e6
    setores["fonte"] = "Núcleos urbanizados — setores de coleta (sem data; vigência a confirmar com a Emlurb)"
    salvar(setores, "setor_coleta", fontes=["setores-de-coleta.geojson"])

    d = geo_ler("drenagem-rede.geojson")
    assert not {"CREATED_USER", "LAST_EDITED_USER"} & set(d.columns), "cópia de trabalho não pode ter autoria"
    n0 = len(d)
    d = d[d.geometry.notna() & ~d.geometry.is_empty]
    d = d[~d.geometry.to_wkb().duplicated()]
    d = d[["OBJECTID", "REDE_TIPO", "geometry"]].rename(columns={"OBJECTID": "trecho_id", "REDE_TIPO": "tipo"})
    d["comprimento_m"] = d.length
    d["fonte"] = "Núcleos urbanizados — Drenagem (rede); sem data na fonte"
    salvar(d, "rede_drenagem", fontes=["drenagem-rede.geojson (cópia sem autoria)"])

    e = geo_ler("rede-esgoto-2022.geojson")
    n0e = len(e)
    e = e[e.geometry.notna() & ~e.geometry.is_empty]
    e = e[~e.geometry.to_wkb().duplicated()]
    e = e[["OBJECTID", "CATEGORIAREDE", "TIPOREDEESGOTO", "OPERADO", "geometry"]].rename(
        columns={"OBJECTID": "trecho_id", "CATEGORIAREDE": "categoria", "TIPOREDEESGOTO": "tipo", "OPERADO": "operado"})
    e["comprimento_m"] = e.length
    e["ano"] = 2022
    e["fonte"] = "Núcleos urbanizados — Rede de esgoto 2022 (pode não incluir toda a rede)"
    salvar(e, "rede_esgoto", fontes=["rede-esgoto-2022.geojson"])
    resumo = {"feicoes_setor": int(len(s)), "invalidas_corrigidas": invalidos, "setores_regulares": int(len(reg)),
              "setores_seletiva": int(len(sel)), "frequencias": reg.frequencia.value_counts().to_dict(),
              "drenagem_trechos": int(len(d)), "drenagem_removidos_vazios_ou_repetidos": int(n0 - len(d)),
              "drenagem_km": float(d.comprimento_m.sum() / 1000), "esgoto_trechos": int(len(e)),
              "esgoto_removidos": int(n0e - len(e)), "esgoto_km": float(e.comprimento_m.sum() / 1000)}
    json_salvar(SILVER / "validacao_nucleos.json", resumo)
    return resumo


# ---------------------------------------------------------------- 17 CIS
def preparar_cis():
    c = geo_ler("favelas.-corticos-e-loteamentos-irregulares-.geojson")
    invalidas = int((~c.is_valid).sum())
    c["geometry"] = shapely.make_valid(c.geometry.array)
    nota = lambda col: numero(c[col]).clip(upper=10)
    out = gpd.GeoDataFrame({
        "cis_geocod": codigo(c.CIS_Geocod), "nome": c.nome_atual.str.strip(), "bairro_predominante": c.Bairro_Pre.map(chave_bairro),
        "unidade_paisagem": c.Unidade_Pa.str.strip(), "populacao_estimada_2014": numero(c.Populacao_).astype("Int64"),
        "imoveis": numero(c.QTDE_IMOVE).astype("Int64"), "nota_coleta_lixo": nota("COLETA_LIX"), "nota_drenagem": nota("DRENAGEM"),
        "nota_alagamento": nota("IND_ALAGAM"), "nota_escorregamento": nota("DENS_ESCOR"), "nota_precariedade": nota("PREC_MAC_C"),
        "classe_precariedade": c.Precarieda.str.strip(), "sanear_prioritaria": c.Prioritari.fillna("").str.contains("SANEAR"),
    }, geometry=c.geometry, crs=c.crs)
    out["area_ha"] = out.area / 1e4
    out["cis_pequena"] = out.imoveis.lt(50).fillna(False)
    out["deficit_coleta"] = out.nota_coleta_lixo.gt(0)
    out["ano"] = 2014
    out["fonte"] = "Atlas das CIS, 2014 (retrato histórico; não é cadastro atual)"
    assert out.cis_geocod.is_unique
    salvar(out, "cis", fontes=["CIS 2014"], nota="uso do gestor; sem ranking nominal; CIS pequena só agregada")
    resumo = {"cis": int(len(out)), "invalidas_corrigidas": invalidas, "area_ha": float(out.area_ha.sum()),
              "populacao_estimada_2014": int(out.populacao_estimada_2014.sum()), "com_deficit_coleta": int(out.deficit_coleta.sum()),
              "deficit_alto_nota_5": int(out.nota_coleta_lixo.ge(5).sum()), "cis_pequenas": int(out.cis_pequena.sum()),
              "sanear": int(out.sanear_prioritaria.sum()), "unidades": out.unidade_paisagem.value_counts().to_dict()}
    json_salvar(SILVER / "validacao_cis.json", resumo)
    return resumo


# ---------------------------------------------------------------- 18 polos de comércio
_SEM = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]
_REGRA_DIAS = {"TODOS OS DIAS": range(7), "QUINTA A SABADO": [3, 4, 5], "SEGUNDAS E TERCAS": [0, 1], "DOMINGO": [6],
               "QUARTA E QUINTA": [2, 3], "SABADOS E DOMINGOS": [5, 6], "SEXTA E SABADO": [4, 5]}


def preparar_polos():
    f = csv_ler(fonte("feiras-livres.csv"))
    f = f.rename(columns={"Nome": "nome", "Dias": "dias_texto", "Horário": "horario_texto", "Latitude": "latitude", "Longitude": "longitude"})
    f["tipo"] = "feira"
    m = csv_ler(fonte("mercados-publicos.csv"))
    m["tipo"] = "mercado"
    m["dias_texto"] = ""
    m["horario_texto"] = ""
    eq = pd.concat([f[["nome", "tipo", "dias_texto", "horario_texto", "latitude", "longitude"]],
                    m[["nome", "tipo", "dias_texto", "horario_texto", "latitude", "longitude"]]], ignore_index=True)
    for i, d in enumerate(_SEM):
        eq[d] = eq.dias_texto.map(lambda t: (i in _REGRA_DIAS[normalizar_nome(t)]) if t else None)
    horas = eq.horario_texto.str.extract(r"(\d{1,2})h\D+(\d{1,2})h")
    eq["abre_h"] = pd.to_numeric(horas[0], errors="coerce")
    eq["fecha_h"] = pd.to_numeric(horas[1], errors="coerce")
    eq["dias_conhecidos"] = eq.dias_texto.ne("")
    eq["coordenada_suspeita"] = eq.nome.eq("Feira de Beberibe")
    pts = _pontos(eq, "latitude", "longitude")
    pts["polo_grupo"] = _agrupar_por_distancia(pts, 250)
    pts["polo_id"] = pts.polo_grupo.map(lambda g: "PL%02d" % (g + 1))
    pts["fonte"] = "CONVIVA — feiras e mercados (cadastro parcial; mercados sem dia e horário na fonte)"
    pts = pts.drop(columns=["polo_grupo", "latitude", "longitude"])
    salvar(pts, "polo_comercio", fontes=["feiras-livres.csv", "mercados-publicos.csv"])
    resumo = {"equipamentos": int(len(pts)), "feiras": int((pts.tipo == "feira").sum()), "mercados": int((pts.tipo == "mercado").sum()),
              "polos": int(pts.polo_id.nunique()), "feiras_abertas_por_dia": {d: int(pts[d].fillna(False).sum()) for d in _SEM},
              "coordenadas_suspeitas": int(pts.coordenada_suspeita.sum())}
    json_salvar(SILVER / "validacao_polos.json", resumo)
    return resumo


# ---------------------------------------------------------------- 19 orçamento
def preparar_orcamento():
    partes = [csv_ler(fonte(f"ppa-e-loa-{a}.csv")) for a in (2023, 2024)]
    o = pd.concat(partes, ignore_index=True)
    o["valor"] = numero(o.valor)
    for col in ("orgao", "unidade", "funcao", "programa", "acao", "categoria", "fonte"):
        div = o[col].str.split(r"\s*-\s*", n=1, expand=True, regex=True)
        o[col + "_codigo"] = div[0].str.strip()
        o[col + "_nome"] = div[1].str.strip()
    # Em 2023 um código de fonte tem várias descrições: não perder essa distinção ao somar.
    chaves = ["ano", "unidade_codigo", "programa_codigo", "acao_codigo", "categoria_codigo", "fonte_codigo", "fonte_nome"]
    nomes = ["orgao_nome", "unidade_nome", "funcao_nome", "programa_nome", "acao_nome", "categoria_nome"]
    g = o.groupby(chaves, as_index=False).agg({**{n: "first" for n in nomes}, "valor": "sum"})
    g["ano"] = g.ano.astype(int)
    g["tipo_valor"] = "LOA (dotação inicial): orçamento previsto, não gasto realizado"
    g["linha_simbolica"] = g.valor.le(10000)
    descricoes_2024 = o[o.ano == '2024'].assign(chave_fonte=lambda d: d.fonte_nome.map(normalizar_nome))
    mapa = descricoes_2024.groupby('chave_fonte').fonte_codigo.agg(lambda s: '|'.join(sorted(set(s))))
    g['fonte_codigo_2024'] = g.fonte_nome.map(normalizar_nome).map(mapa)
    g['equivalencia_fonte'] = np.where(g.fonte_codigo_2024.notna() & ~g.fonte_codigo_2024.fillna('').str.contains('|', regex=False),
                                      'descrição normalizada idêntica; conferir com órgão', 'sem equivalência inequívoca')
    salvar(g[['ano','fonte_codigo','fonte_nome','fonte_codigo_2024','equivalencia_fonte']].drop_duplicates(),
           'equivalencia_fontes_orcamento', fontes=['PPA e LOA 2023 e 2024'], nota='sem correspondência presumida por código antigo')
    g["zeladoria"] = np.select([g.acao_codigo.eq("2509") & g.unidade_codigo.eq("5010"), g.acao_codigo.eq("2543") & g.unidade_codigo.eq("5010")],
                               ["limpeza urbana (Emlurb, ação 2509)", "drenagem (Emlurb, ação 2543)"], "")
    salvar(g, "orcamento_acao", fontes=["PPA e LOA 2023 e 2024"])
    tot = g.groupby("ano").valor.sum()
    z = g[g.zeladoria != ""].groupby(["ano", "zeladoria"]).valor.sum()
    resumo = {"linhas_fonte": int(len(o)), "linhas_silver": int(len(g)), "total_loa_mi": {int(a): round(v / 1e6, 1) for a, v in tot.items()},
              "zeladoria_mi": {f"{a} {n}": round(v / 1e6, 1) for (a, n), v in z.items()}}
    json_salvar(SILVER / "validacao_orcamento.json", resumo)
    return resumo
