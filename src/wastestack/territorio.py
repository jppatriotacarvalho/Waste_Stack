"""Silver territorial: relações explícitas e cobertura de ambas as bordas."""
import json
import h3
import geopandas as gpd
import pandas as pd
from shapely import Polygon, union_all

from .arquivos import fonte, csv_ler, codigo, numero, geo_ler, salvar, json_salvar
from .config import SILVER, EPSG_METROS
from .espacial import preparar_contorno
from .limpeza import chave_bairro, normalizar_nome


def preparar_territorio():
    contorno=preparar_contorno(fonte('PE_Municipios_2025.zip'),fonte('bairros-do-recife.geojson'),SILVER)
    b=geo_ler('bairros-do-recife.geojson')
    # Somente atributos necessários; colunas de manutenção não se propagam.
    b=b[['CBAIRRCODI','EBAIRRNOME','EBAIRRNOMEOF','CRPAAACODI','geometry']].rename(columns={
        'CBAIRRCODI':'bairro_codigo','EBAIRRNOME':'bairro_chave','EBAIRRNOMEOF':'bairro_nome','CRPAAACODI':'rpa'})
    b['bairro_codigo']=codigo(b['bairro_codigo'])
    b['bairro_chave']=b['bairro_chave'].map(chave_bairro)
    b['area_km2']=b.area/1e6
    b['fonte']='Área urbana / bairros; referência documental 2019'
    assert b.bairro_codigo.is_unique and b.bairro_chave.is_unique
    salvar(b,'territorio_bairro',fontes=['Área urbana / bairros 2019'])
    rpa=csv_ler(fonte('bairros-e-rpas-do-recife.csv'))
    rpa['bairro_chave']=rpa.Bairro.map(chave_bairro)
    rb=rpa.merge(b.drop(columns='geometry'),on='bairro_chave',suffixes=('_csv','_geo'),validate='one_to_one')
    assert len(rb)==len(b) and (numero(rb.rpa_csv)==numero(rb.rpa_geo)).all()

    ruas=geo_ler('trechos-de-logradouros.geojson')
    ruas['logradouro_codigo']=codigo(ruas.CLOGRACODI)
    ruas['logradouro_norm']=ruas.NLOGRACONC.map(normalizar_nome)
    # Rios cadastrados como rua: o nome original termina em "-RIO"/"-RIACHO" (com hífen), além do Canal do Jordão.
    # Ruas de verdade como "RUA BEIRA RIO" ou "RUA DO RIO" permanecem na malha viária.
    rios=ruas.NLOGRACONC.fillna('').str.contains(r'-\s*(?:RIO|RIACHO)\s*$',regex=True) | ruas.logradouro_norm.eq('RUA CANAL DO JORDAO')
    limite=ruas.logradouro_norm.eq('RUA LIMITE DE MUNICIPIO')
    salvar(ruas.loc[rios,['logradouro_codigo','logradouro_norm','geometry']], 'linhas_agua_logradouro',fontes=['Área urbana / trechos 2023'])
    ruas=ruas.loc[~rios & ~limite,['logradouro_codigo','logradouro_norm','geometry']].copy()
    ruas=ruas.dissolve(by='logradouro_codigo',aggfunc='first').reset_index()
    salvar(ruas,'malha_logradouros',fontes=['Área urbana / trechos 2023'])
    rel=csv_ler(fonte('trechos-de-logradouros-por-bairro.csv'))
    rel['logradouro_codigo']=codigo(rel.codlogradouro)
    rel['bairro_codigo']=codigo(rel.codbairro)
    rel['logradouro_norm']=rel.nome_logradouro_concatenado.map(normalizar_nome)
    rel=rel[['logradouro_codigo','bairro_codigo','logradouro_norm']].drop_duplicates()
    rel=rel.merge(b[['bairro_codigo','bairro_chave']],on='bairro_codigo',how='left',validate='many_to_one')
    amb=rel.groupby(['bairro_codigo','logradouro_norm']).logradouro_codigo.transform('nunique').gt(1)
    rel['nome_ambiguo']=amb | rel.logradouro_norm.str.contains('FALTA DENOMINACAO',na=False)
    rel=rel.merge(ruas[['logradouro_codigo','geometry']],on='logradouro_codigo',how='left',validate='many_to_one')
    geometria_bairro=b.set_index('bairro_codigo').geometry.to_dict()
    rel['geometry']=[g.intersection(geometria_bairro[c]) if g is not None and c in geometria_bairro else None
        for g,c in zip(rel.geometry,rel.bairro_codigo)]
    rel=gpd.GeoDataFrame(rel,geometry='geometry',crs=EPSG_METROS)
    rel['comprimento_m']=rel.length.fillna(0)
    rel['metodo']='código oficial + bairro; linha recortada; nenhum endereço estimado'
    # Ruas de borda: a relação oficial rua-bairro existe, mas a linha corre do lado de fora do polígono.
    # Usa-se o trecho a até 30 m do limite do bairro, com o método registrado (não inventa posição).
    linha_inteira=ruas.set_index('logradouro_codigo').geometry.to_dict()
    for i in rel.index[rel.comprimento_m<5]:
        g=linha_inteira.get(rel.at[i,'logradouro_codigo']); c=rel.at[i,'bairro_codigo']
        if g is None or c not in geometria_bairro: continue
        perto=g.intersection(geometria_bairro[c].buffer(30))
        if perto.length>=5:
            rel.at[i,'geometry']=perto; rel.at[i,'comprimento_m']=perto.length
            rel.at[i,'metodo']='código oficial + bairro; rua de borda: trecho a até 30 m do limite do bairro'
    rel['ligacao_geometrica_valida']=rel.geometry.notna() & ~rel.geometry.is_empty.fillna(True) & (rel.comprimento_m>=5)
    # Preserva relações sem linha e trechos curtos para auditoria, sem utilizá-los na localização.
    salvar(rel,'territorio_logradouro',fontes=['Área urbana / relação rua-bairro e trechos 2023'])

    agua=geo_ler('recursos-hidricos.geojson')
    agua['nome']=agua.NMRECHIDRIC.fillna('').str.strip().replace('','(sem nome)')
    def tipo(t):
        n=normalizar_nome(t)
        for word,clas in [('OCEAN','oceano'),('BACIA','bacia'),('ALAGAD','alagado'),('ACUDE','acude'),('LAG','lagoa'),('RIACHO','riacho'),('CANAL','canal'),('RIO','rio')]:
            if word in n:return clas
        return 'não classificado'
    agua['classe']=agua.nome.map(tipo)
    agua['area_m2']=agua.area
    agua['usar_distancia']=agua.classe.isin(['rio','canal','riacho']) & agua.area.ge(10)
    agua=agua[['OBJECTID','nome','classe','area_m2','usar_distancia','geometry']].rename(columns={'OBJECTID':'agua_id'})
    salvar(agua,'territorio_agua',fontes=['Área urbana / recursos hídricos; período desconhecido'])

    faces=csv_ler(fonte('logradouros-por-face-de-quadra.csv'))
    faces['bairro_codigo']=codigo(faces.codbairro)
    faces['logradouro_codigo']=codigo(faces.codlogradouro)
    faces['v0']=numero(faces.valor_v0).where(lambda s:s>0)
    faces['coleta_manual']=numero(faces.codcoleta).isin([5,6])
    faces['limpeza_mensal_ou_sem']=numero(faces.codlimpezaurbana).isin([0,4])
    faces['sem_galeria']=numero(faces.codgaleriapluviais).eq(2)
    faces['sem_esgoto']=numero(faces.codredesgoto).eq(2)
    faces['escadaria']=numero(faces.codpavimentacao).eq(6)
    coleta={0:'sem coleta',1:'convencional diária com seletiva',2:'convencional diária sem seletiva',3:'convencional alternada com seletiva',4:'convencional alternada sem seletiva',5:'manual diária',6:'manual alternada'}
    limpeza={0:'sem limpeza',1:'regular diária',2:'regular alternada',3:'programada semanal',4:'programada mensal'}
    faces['coleta_plano']=numero(faces.codcoleta).map(coleta)
    faces['limpeza_plano']=numero(faces.codlimpezaurbana).map(limpeza)
    keys=set(zip(rel.loc[rel.ligacao_geometrica_valida,'logradouro_codigo'],rel.loc[rel.ligacao_geometrica_valida,'bairro_codigo']))
    faces['tem_linha']= [t in keys for t in zip(faces.logradouro_codigo,faces.bairro_codigo)]
    faces['localizacao']='atributos por rua/bairro; geometria da face indisponível'
    faces['referencia']='data desconhecida; vigência não confirmada'
    assert faces.dsqf.is_unique
    salvar(faces,'face_quadra',fontes=['Área urbana / faces; data desconhecida'])

    municipio=gpd.read_parquet(SILVER/'contorno_municipal_ibge_2025.parquet').to_crs(EPSG_METROS).geometry.iloc[0]
    uniao_bairros=union_all(b.geometry.array)
    dominio=municipio.union(uniao_bairros)
    dominio_wgs=gpd.GeoSeries([dominio],crs=EPSG_METROS).to_crs(4326).iloc[0]
    # Centros da área + vizinhança garantem células de borda; conserva hexágonos inteiros.
    centros=h3.geo_to_cells(dominio_wgs.__geo_interface__,9)
    candidatos=set(centros)
    for cell in centros:candidatos.update(h3.grid_disk(cell,1))
    grid=gpd.GeoDataFrame({'h3':sorted(candidatos)},geometry=[Polygon([(lon,lat) for lat,lon in h3.cell_to_boundary(c)]) for c in sorted(candidatos)],crs=4326).to_crs(EPSG_METROS)
    grid=grid.loc[grid.intersects(dominio)].copy().reset_index(drop=True)
    grid['area_celula_m2']=grid.area
    grid['area_dominio_m2']=grid.intersection(dominio).area
    grid['fracao_ibge']=grid.intersection(municipio).area/grid.area
    grid['fracao_bairros']=grid.intersection(uniao_bairros).area/grid.area
    grid['borda_divergente']=grid.intersection(municipio.symmetric_difference(uniao_bairros)).area.gt(0.01)
    grid['area_urbanizada_m2']=float('nan')
    grid['nota_urbanizacao']='não estimada: camada urbana comprovada não recebida'
    pairs=gpd.overlay(grid[['h3','geometry']],b[['bairro_codigo','rpa','geometry']],how='intersection',keep_geom_type=False)
    pairs['area_intersecao_m2']=pairs.area
    major=pairs.sort_values(['area_intersecao_m2','bairro_codigo'],ascending=[False,True]).drop_duplicates('h3')
    grid=grid.merge(major[['h3','bairro_codigo','rpa']],on='h3',how='left',validate='one_to_one')
    grid['politica_borda']='união IBGE 2025 + bairros; ambos preservados; maior interseção define bairro de referência'
    missing=dominio.difference(union_all(grid.geometry.array)).area
    assert missing<0.01, f'Grade não cobre domínio: {missing}'
    salvar(grid,'grade_h3',fontes=['IBGE Malha 2025','Área urbana bairros 2019','H3 resolução 9'])
    salvar(pairs.drop(columns='geometry'),'grade_h3_bairro',fontes=['interseção H3 x bairros'],nota='relações de área; não distribui população')
    stats={'bairros':len(b),'relacoes_rua_bairro':len(rel),'relacoes_sem_linha_util':int((~rel.ligacao_geometrica_valida).sum()),
        'relacoes_ambiguas':int(rel.nome_ambiguo.sum()),'rios_removidos_malha':int(rios.sum()),'faces':len(faces),
        'faces_sem_linha':int((~faces.tem_linha).sum()),'agua':len(agua),'h3':len(grid),'area_sem_cobertura_m2':missing,
        'rpa_por_campo_conferida':True,'politica':'união das duas fontes; não recortar bairros; pontos nas divergências sinalizados'}
    json_salvar(SILVER/'validacao_territorio.json',stats)
    return stats
