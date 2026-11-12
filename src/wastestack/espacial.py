"""Preparação e comparação dos contornos municipais usados no território."""

from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path

import geopandas as gpd
from shapely import union_all

from .bronze import inspecionar_arquivo
from .config import EPSG_METROS


def _validar_poligonos(tabela, nome):
    if tabela.crs is None:
        raise ValueError(f"{nome}: sistema de coordenadas não declarado")
    if tabela.empty or tabela.geometry.isna().any() or tabela.geometry.is_empty.any():
        raise ValueError(f"{nome}: geometrias ausentes ou vazias")
    if not tabela.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError(f"{nome}: esperados polígonos")
    if not tabela.is_valid.all():
        raise ValueError(f"{nome}: geometria inválida; investigar antes de corrigir")


def preparar_contorno(arquivo_ibge, arquivo_bairros, pasta_saida):
    """Separa Recife e salva a comparação, sem recortar ou substituir bairros.

    As duas entradas são Bronze. Todas as medidas são calculadas em
    EPSG:31985. A área do atributo IBGE é preservada separadamente da área
    projetada; a comparação não estabelece a causa das diferenças nas bordas.
    """
    arquivo_ibge = Path(arquivo_ibge).resolve()
    arquivo_bairros = Path(arquivo_bairros).resolve()
    pasta_saida = Path(pasta_saida)
    municipios = gpd.read_file("zip://" + arquivo_ibge.as_posix())
    recife = municipios.loc[municipios["CD_MUN"].astype(str).eq("2611606")].copy()
    if len(recife) != 1 or recife.iloc[0]["NM_MUN"] != "Recife":
        raise ValueError("Esperado um único Recife, código IBGE 2611606")
    bairros = gpd.read_file(arquivo_bairros)
    chave = bairros["CBAIRRCODI"]
    if chave.isna().any() or not chave.is_unique:
        raise ValueError("Código de bairro ausente ou repetido")
    _validar_poligonos(recife, "Recife/IBGE")
    _validar_poligonos(bairros, "Bairros/Recife")
    crs_fontes = {"ibge": str(recife.crs), "bairros": str(bairros.crs)}
    recife_m = recife.to_crs(EPSG_METROS)
    bairros_m = bairros.to_crs(EPSG_METROS)
    municipio = recife_m.geometry.iloc[0]
    uniao = union_all(bairros_m.geometry.array)
    if not uniao.is_valid or uniao.is_empty:
        raise ValueError("União dos bairros inválida ou vazia")
    comum = municipio.intersection(uniao)
    sem_bairro = municipio.difference(uniao)
    fora_ibge = uniao.difference(municipio)

    # Identidades de área conferem a operação; não forçam fontes a coincidir.
    tolerancia_m2 = max(municipio.area, uniao.area) * 1e-9
    if abs(municipio.area - comum.area - sem_bairro.area) > tolerancia_m2:
        raise ValueError("Inconsistência na decomposição da área municipal")
    if abs(uniao.area - comum.area - fora_ibge.area) > tolerancia_m2:
        raise ValueError("Inconsistência na decomposição da área dos bairros")

    contorno = recife_m[["CD_MUN", "NM_MUN", "AREA_KM2", "geometry"]].rename(
        columns={"CD_MUN": "municipio_codigo", "NM_MUN": "municipio_nome", "AREA_KM2": "area_atributo_ibge_km2"}
    )
    contorno["area_geometria_utm_km2"] = municipio.area / 1e6
    contorno["fonte"] = "IBGE — Malha Municipal Digital 2025"
    contorno["ano_referencia"] = 2025
    uniao_bairros = gpd.GeoDataFrame(
        {"fonte": ["Prefeitura do Recife — Área urbana"], "bairros": [len(bairros)]},
        geometry=[uniao], crs=EPSG_METROS,
    )
    divergencias = gpd.GeoDataFrame(
        {"tipo": ["ibge_sem_bairro", "bairro_fora_ibge"]},
        geometry=[sem_bairro, fora_ibge], crs=EPSG_METROS,
    )
    divergencias["area_km2"] = divergencias.area / 1e6
    cobertura = bairros_m[["CBAIRRCODI", "EBAIRRNOMEOF", "CRPAAACODI", "geometry"]].rename(
        columns={"CBAIRRCODI": "bairro_codigo", "EBAIRRNOMEOF": "bairro_nome", "CRPAAACODI": "rpa"}
    ).copy()
    cobertura["area_bairro_km2"] = cobertura.area / 1e6
    cobertura["area_fora_ibge_km2"] = cobertura.geometry.map(lambda g: g.difference(municipio).area / 1e6)
    cobertura["pct_area_fora_ibge"] = 100 * cobertura["area_fora_ibge_km2"] / cobertura["area_bairro_km2"]
    resumo = {
        "executado_em": datetime.now(timezone.utc).isoformat(),
        "status": "contorno separado e comparação executada; notebook 10 parcial",
        "municipio_codigo": "2611606",
        "municipio_nome": "Recife",
        "municipios_na_fonte": len(municipios),
        "bairros": len(bairros),
        "geometrias_originais_validas": True,
        "geometrias_corrigidas": 0,
        "codigos_bairro_unicos": True,
        "crs_fontes": crs_fontes,
        "epsg_medidas": EPSG_METROS,
        "area_atributo_ibge_km2": float(recife.iloc[0]["AREA_KM2"]),
        "area_ibge_utm_km2": municipio.area / 1e6,
        "area_uniao_bairros_utm_km2": uniao.area / 1e6,
        "soma_menos_uniao_bairros_m2": float(bairros_m.area.sum() - uniao.area),
        "area_comum_km2": comum.area / 1e6,
        "ibge_sem_bairro_km2": sem_bairro.area / 1e6,
        "bairros_fora_ibge_km2": fora_ibge.area / 1e6,
        "pct_ibge_coberto_bairros": 100 * comum.area / municipio.area,
        "pct_bairros_no_ibge": 100 * comum.area / uniao.area,
        "fontes": [
            {"arquivo": arquivo_ibge.name, "sha256": inspecionar_arquivo(arquivo_ibge)["sha256"]},
            {"arquivo": arquivo_bairros.name, "sha256": inspecionar_arquivo(arquivo_bairros)["sha256"]},
        ],
        "versoes": {nome: version(nome) for nome in ("geopandas", "shapely", "pyproj", "pyarrow")},
        "interpretacao": [
            "IBGE e Prefeitura são fontes distintas; diferenças não comprovam erro ou mudança legal.",
            "A área do atributo IBGE e a medida UTM usam procedimentos distintos; foram preservadas separadamente.",
            "Nenhum bairro foi recortado. Áreas presentes em apenas uma fonte foram preservadas para investigação.",
            "Esta etapa não define descarte automático de coordenadas ou cobertura final da grade H3.",
        ],
    }
    pasta_saida.mkdir(parents=True, exist_ok=True)
    saidas = {
        "contorno_municipal_ibge_2025.parquet": contorno,
        "contorno_uniao_bairros.parquet": uniao_bairros,
        "divergencias_contorno.parquet": divergencias,
        "cobertura_bairro_contorno.parquet": cobertura,
    }
    for nome, tabela in saidas.items():
        tabela.to_parquet(pasta_saida / nome, index=False)
        releitura = gpd.read_parquet(pasta_saida / nome)
        if len(releitura) != len(tabela) or releitura.crs != tabela.crs:
            raise ValueError(f"{nome}: saída diverge na releitura")
        if not releitura.geometry.geom_equals(tabela.geometry.reset_index(drop=True)).all():
            raise ValueError(f"{nome}: geometria diverge na releitura")
    (pasta_saida / "validacao_contorno.json").write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return resumo
