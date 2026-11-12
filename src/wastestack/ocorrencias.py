"""Preparação reproduzível do 156 sem número de imóvel."""
import re
import pandas as pd
from wastestack.config import BRONZE, SILVER, LAT_MIN, LAT_MAX, LON_MIN, LON_MAX
from wastestack.limpeza import consertar_coordenada, chave_bairro, normalizar_nome


from wastestack.arquivos import salvar

def preparar_156():
    ARQUIVOS = {2024: "156_2024.csv", 2025: "156_2025.csv", 2026: "156_2026.csv"}
    
    partes = []
    for ano, nome in ARQUIVOS.items():
        df = pd.read_csv(BRONZE / "156" / nome, dtype=str, keep_default_na=False)
        # A coluna NUMERO veio esvaziada e com cabeçalho em branco: sai aqui
        df = df.drop(columns=[c for c in df.columns if c.startswith("Unnamed") or c.strip() == ""])
        df = df.apply(lambda s: s.str.strip())
        df["arquivo_ano"] = ano
        partes.append(df)
        print(ano, len(df), "linhas")
    
    bruto = pd.concat(partes, ignore_index=True)
    print("Total empilhado:", len(bruto))
    
    CHAVE = ["SERVICO_CODIGO", "LOGRADOURO", "BAIRRO", "DATA_DEMANDA"]
    
    ordenado = bruto.sort_values(["arquivo_ano", "DATA_ULT_SITUACAO"], ascending=[False, False])
    dedup = ordenado.drop_duplicates(subset=CHAVE, keep="first").copy()
    
    entre_arquivos = bruto.groupby(CHAVE)["arquivo_ano"].nunique().gt(1).sum()
    print("Demandas depois da deduplicação:", len(dedup))
    print("Linhas removidas:", len(bruto) - len(dedup))
    print("Demandas que aparecem em mais de um arquivo:", entre_arquivos)
    
    # Espaços triplos no lugar de hífen ("Cohab   Ibura de Cima" -> "Cohab - Ibura de Cima")
    dedup["BAIRRO"] = dedup["BAIRRO"].str.replace(r"\s{3,}", " - ", regex=True)
    dedup.loc[dedup["BAIRRO"].str.upper().isin(["BAIRRO NAO INFORMADO", "INDEFINIDO", ""]), "BAIRRO"] = "Não informado"
    dedup["bairro_chave"] = dedup["BAIRRO"].map(chave_bairro)
    
    # Logradouro: nome normalizado (para casar com a Área urbana no notebook 21) e tipo de via
    dedup["logradouro_norm"] = dedup["LOGRADOURO"].map(normalizar_nome)
    dedup["tipo_via"] = dedup["logradouro_norm"].str.split(" ").str[0]
    VIAS_PRECARIAS = {"BEC", "COR", "VIL", "LAD", "ESC"}
    dedup["via_precaria"] = dedup["tipo_via"].isin(VIAS_PRECARIAS) | dedup["logradouro_norm"].str.contains("CORREGO", na=False)
    
    print("Bairros distintos:", dedup["BAIRRO"].nunique())
    dedup["tipo_via"].value_counts().head(10)
    
    dedup["lat"] = dedup["latitude"].map(lambda v: consertar_coordenada(v, 1))
    dedup["lon"] = dedup["longitude"].map(lambda v: consertar_coordenada(v, 2))
    
    dentro = dedup["lat"].between(LAT_MIN, LAT_MAX) & dedup["lon"].between(LON_MIN, LON_MAX)
    dedup["coord_valida"] = dentro
    dedup.loc[~dentro, ["lat", "lon"]] = None
    dedup = dedup.drop(columns=["latitude", "longitude"])
    
    print("Com coordenada válida:", dentro.sum(), f"({dentro.mean():.1%})")
    dedup.groupby("arquivo_ano")["coord_valida"].mean().round(3)
    
    RECORTE = {
        "Lixo e descarte": [6, 100, 212, 207, 209, 16, 208, 149, 203, 304, 310, 112],
        "Limpeza de vias": [80, 214, 167, 102, 177, 84, 201, 147],
        "Limpeza de drenagem": [8, 157, 26],
    }
    codigo_para_categoria = {str(c): cat for cat, cods in RECORTE.items() for c in cods}
    dedup["categoria"] = dedup["SERVICO_CODIGO"].map(codigo_para_categoria)
    dedup["no_recorte"] = dedup["categoria"].notna()
    
    dedup["data_demanda"] = pd.to_datetime(dedup["DATA_DEMANDA"], errors="coerce")
    dedup["data_ult_situacao"] = pd.to_datetime(dedup["DATA_ULT_SITUACAO"], errors="coerce")
    
    atendida = dedup["SITUACAO"].eq("ATENDIDA")
    dedup["dias_ate_atender"] = (dedup["data_ult_situacao"] - dedup["data_demanda"]).dt.days.where(atendida)
    
    # Meses completos do 156: jan/2024–ago/2025 e jan–mai/2026 (o arquivo de 2025 para em 10/09/2025)
    mes = dedup["data_demanda"].dt.to_period("M")
    dedup["mes_completo"] = (
        ((mes >= pd.Period("2024-01")) & (mes <= pd.Period("2025-08")))
        | ((mes >= pd.Period("2026-01")) & (mes <= pd.Period("2026-05")))
    )
    dedup["periodo_analise"] = dedup["data_demanda"].between("2024-01-01", "2026-06-19")
    
    COLUNAS = [
        "GRUPOSERVICO_CODIGO", "GRUPOSERVICO_DESCRICAO", "SERVICO_CODIGO", "SERVICO_DESCRICAO", "categoria", "no_recorte",
        "LOGRADOURO", "logradouro_norm", "tipo_via", "via_precaria",
        "BAIRRO", "bairro_chave", "RPA",
        "data_demanda", "SITUACAO", "data_ult_situacao", "dias_ate_atender", "mes_completo", "periodo_analise",
        "lat", "lon", "coord_valida", "arquivo_ano",
    ]
    silver = dedup[COLUNAS].rename(columns=str.lower).reset_index(drop=True)
    SILVER.mkdir(parents=True, exist_ok=True)
    salvar(silver, "ocorrencia_156", fontes=list(ARQUIVOS.values()), nota="Chave sem NUMERO; versão autorizada. Sem endereço individual publicável.")
    print("Gravado:", SILVER / "ocorrencia_156.parquet", len(silver), "linhas")
    return silver, len(bruto)
