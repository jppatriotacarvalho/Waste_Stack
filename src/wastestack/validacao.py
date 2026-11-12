"""Conferências reproduzíveis das fontes e das saídas; nenhum valor pessoal é impresso."""
import hashlib
import importlib.metadata
import json
from pathlib import Path

import geopandas as gpd
import h3
import numpy as np
import pandas as pd
from shapely import union_all

from .arquivos import agora, conferir_bronze, fonte, json_salvar, ler
from .config import RAIZ, SILVER, GOLD, EPSG_METROS


def conferir_dicionario():
    """Lê os significados efetivos do XLSX oficial; não infere pela ordem das colunas."""
    from openpyxl import load_workbook
    p = next((RAIZ / 'docs/dicionarios/ibge').rglob('*.xlsx'))
    wb = load_workbook(p, read_only=True, data_only=True)
    achados = {}
    for folha in wb:
        if 'básico' not in folha.title.lower() and 'basico' not in folha.title.lower():
            continue
        for row in folha.iter_rows(values_only=True):
            itens = [str(v).strip() for v in row if v is not None]
            for i, valor in enumerate(itens):
                if valor.lower() in {f'v{x:04d}' for x in range(1, 10)}:
                    achados[valor.lower()] = {'aba': folha.title, 'descricao': ' | '.join(itens[i + 1:])}
    wb.close()
    assert len(achados) == 9, 'Não foram encontrados v0001 a v0009 no dicionário básico'
    assert 'Total de pessoas' in achados['v0001']['descricao']
    evidencia = {'arquivo': p.relative_to(RAIZ).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                 'versao': '20260520', 'conferido_em': agora(), 'variaveis': achados,
                 'url_oficial': 'https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/dicionario_de_dados_agregados_por_setores_censitarios_20260520.xlsx'}
    json_salvar(RAIZ / 'data/validacao/dicionario_ibge.json', evidencia)
    return evidencia


def validar_dados():
    verificacoes = []
    def verificar(nome, condicao, detalhe=''):
        verificacoes.append({'verificacao': nome, 'aprovada': bool(condicao), 'detalhe': detalhe})

    bronze = conferir_bronze()
    conferir_dicionario()
    verificar('integridade Bronze', bronze['entradas'] >= 28, bronze)
    b, grade = ler('territorio_bairro', geo=True), ler('grade_h3', geo=True)
    pop = ler('populacao_bairro')
    verificar('94 bairros e população sem perda de ligação', len(b) == len(pop) == 94 and set(b.bairro_codigo) == set(pop.bairro_codigo))
    verificar('população Censo 2022', pop.populacao.sum() == 1488920, int(pop.populacao.sum()))
    verificar('chaves de bairro únicas', b.bairro_codigo.is_unique and pop.bairro_codigo.is_unique)
    verificar('H3 resolução 9 e chave única', grade.h3.is_unique and all(h3.get_resolution(c) == 9 for c in grade.h3))
    from .silver import _dominio
    verificar('grade cobre as duas bordas', _dominio().difference(union_all(grade.geometry)).area < 0.01)
    geometrias = ['territorio_bairro', 'grade_h3', 'faixa_marginal', 'cis', 'setor_coleta', 'rede_drenagem', 'rede_esgoto']
    for nome in geometrias:
        g = ler(nome, geo=True)
        verificar('geometrias válidas: ' + nome, g.is_valid.all() and not g.is_empty.any() and g.crs.to_epsg() == EPSG_METROS)
    faces = ler('face_quadra')
    verificar('faces sem localização inventada', faces.dsqf.is_unique and len(faces) == 54227 and 'geometry' not in faces)
    verificar('V0 zero tratado como ausência', (faces.v0.dropna() > 0).all())
    o = ler('ocorrencia_156')
    rec = o[o.no_recorte & o.periodo_analise]
    verificar('156 deduplicação autorizada', len(o) == 230491 and len(rec) == 61790, {'demandas': len(o), 'recorte': len(rec)})
    verificar('156 sem número de imóvel', 'numero' not in o.columns)
    verificar('156 chave composta única', not o.duplicated(['servico_codigo','logradouro','bairro','data_demanda']).any())
    verificar('25 meses completos', o[o.mes_completo].data_demanda.dt.to_period('M').nunique() == 25)
    loc = ler('ocorrencia_156_localizada')
    verificar('localização conserva uma demanda', np.allclose(loc.groupby('ocorrencia_id').peso.sum(), 1, atol=1e-8))
    verificar('relações localização válidas', set(loc.h3) <= set(grade.h3) and set(loc.ocorrencia_id) <= set(range(len(o))))
    hist = ler('motor_historico_h3_mes', camada=GOLD)
    esperados = int(o.loc[sorted(set(loc.ocorrencia_id))].eval('no_recorte & mes_completo').sum())
    verificar('Gold histórico conserva soma da Silver', np.isclose(hist.chamados.sum(), esperados), esperados)
    verificar('histórico sem meses parciais', set(hist.mes) == set(o[o.mes_completo].data_demanda.dt.strftime('%Y-%m')))
    dr = json.loads(fonte('drenagem-rede.geojson').read_text(encoding='utf-8'))
    verificar('drenagem sem autoria em todas as feições', all(not {'CREATED_USER','LAST_EDITED_USER'} & set(f['properties']) for f in dr['features']))
    original = json.loads((RAIZ / 'data/bronze/_restrito/nucleos_urbanizados/2026-11-11/drenagem-rede_original.geojson').read_text(encoding='utf-8'))
    for f in original['features']:
        for campo in ('CREATED_USER', 'LAST_EDITED_USER'):
            f['properties'].pop(campo, None)
    verificar('drenagem mantém conteúdo restante do original', original == dr)
    dc = pd.read_parquet(SILVER / '_restrito/acao_defesa_civil.parquet')
    verificar('Defesa Civil só arquivos 2023/2024', set(dc.arquivo_ano) == {2023,2024} and len(dc) == 163337)
    verificar('lona inclui registros sem rótulo de lona', np.isclose(dc.area_lona_m2.sum(), 6930688), float(dc.area_lona_m2.sum()))
    enc = ler('risco_encosta_bairro_mes', camada=GOLD)
    verificar('Defesa Civil Gold sem rua ou dia individual', not {'logradouro_norm','localidade','endereco','data'} & set(enc))
    cis = ler('camada_cis_gestor', camada=GOLD)
    verificar('CIS pequenas fora da camada nominal', (cis.imoveis >= 50).all() and (cis.uso == 'gestor').all())
    rl = ler('base156_reincidencia_logradouro', camada=GOLD)
    verificar('ruas 156 bloqueadas por endereços não verificáveis', not rl.publicavel.any() and (rl.uso == 'gestor').all())
    pontos = ler('app_onde_descartar', camada=GOLD)
    verificar('descarte tem origem e situação honesta', pontos.ponto_id.is_unique and pontos.fontes.notna().all()
              and pontos.situacao.eq('histórico, a confirmar').all() and pontos.data_verificacao.eq('desconhecida').all())
    verificar('sete ecoestações sem coordenada inventada', ((pontos.tipo == 'ecoestação') & pontos.lat.isna()).sum() == 7)
    verificar('orçamento preserva totais', ler('orcamento_acao').groupby('ano').valor.sum().to_dict() == {2023:7259000000,2024:8234000000})
    feat = ler('motor_features_h3', camada=GOLD)
    verificar('regime incerto explícito', set(feat.regime) <= {'morro','planicie','indefinido'} and feat.regime.eq('indefinido').any())
    score = ler('motor_score_h3_semana', camada=GOLD)
    verificar('score finito e entre 0 e 100', score.score.between(0,100).all())
    cmp = ler('motor_comparacao_esforco', camada=GOLD)
    verificar('comparação mesma unidade cobertura e esforço', cmp.universo_h3.nunique() == cmp.alvo_localizado_comum.nunique() == 1
              and np.allclose(cmp.via_selecionada_km, cmp.orcamento_via_km))
    # Nota metodológica da MMD (edição 2024, Notas metodológicas 01/2025): AREA_KM2 vem da Albers equivalente do IBGE.
    contorno = gpd.read_parquet(SILVER / 'contorno_municipal_ibge_2025.parquet')
    albers = '+proj=aea +lat_0=-12 +lon_0=-54 +lat_1=-2 +lat_2=-22 +x_0=5000000 +y_0=10000000 +ellps=GRS80 +units=m +no_defs'
    area_albers = float(contorno.to_crs(albers).area.iloc[0] / 1e6)
    verificar('AREA_KM2 do IBGE reproduzido na Albers do IBGE', abs(area_albers - float(contorno.area_atributo_ibge_km2.iloc[0])) < 0.001,
              {'albers_km2': round(area_albers, 4), 'atributo_km2': float(contorno.area_atributo_ibge_km2.iloc[0]),
               'utm_31985_km2': round(float(contorno.to_crs(EPSG_METROS).area.iloc[0] / 1e6), 4)})
    fila156 = ler('base156_fila_aberta', camada=GOLD)
    verificar('fila do 156 separa abertas com movimento das paradas', set(fila156.movimento) == {'aberta com movimento em 2026', 'parada desde 2024–2025'})
    vias = gpd.read_parquet(GOLD / 'mapa_base_vias.parquet')
    verificar('mapa base de vias só com linhas', vias.geom_type.isin(['LineString', 'MultiLineString']).all(), vias.geom_type.value_counts().to_dict())
    resumo = json.loads((GOLD / 'dados_publicos_resumo.json').read_text(encoding='utf-8'))
    verificar('resumo da página Recife em Dados com 8 temas, fonte e período', len(resumo['temas']) == 8
              and all(t['fonte'] and t['periodo'] for t in resumo['temas'].values()))
    ex = ler('app_exemplos_simulados', camada=GOLD)
    verificar('exemplos identificados', ex.origem.eq('simulado').all())
    # Cada arquivo gerado com metadado deve ter hash atual e contagem consistente.
    for pasta in (SILVER, GOLD):
        for p in pasta.rglob('*.meta.json'):
            meta = json.loads(p.read_text(encoding='utf-8'))
            arq = p.with_name(p.name.replace('.meta.json','.parquet'))
            verificar('releitura e hash ' + arq.stem, arq.exists() and hashlib.sha256(arq.read_bytes()).hexdigest() == meta['sha256']
                      and len(pd.read_parquet(arq)) == meta['registros'])
    rel = {'executado_em': agora(), 'aprovado': all(x['aprovada'] for x in verificacoes), 'verificacoes': verificacoes,
           'versoes': {n: importlib.metadata.version(n) for n in ('pandas','geopandas','h3','duckdb','pyarrow','fastapi','pytest')},
           'limites': ['endereços distintos do 156 não verificáveis', 'C7 antes/depois depende das coordenadas das unidades novas',
                      'nota metodológica MMD 2025 indisponível (403/fora da lista de acesso); consultada a edição 2024, que explica AREA_KM2 em Albers', 'fotografias desativadas', 'testes com pessoas e operação real não realizados']}
    json_salvar(RAIZ / 'data/validacao/dados.json', rel)
    falhas = [x['verificacao'] for x in verificacoes if not x['aprovada']]
    if falhas:
        raise AssertionError('Falhas na validação: ' + '; '.join(falhas))
    return {'aprovado': True, 'verificacoes': len(verificacoes), 'bronze': bronze['entradas'], 'bairros': len(b), 'hexagonos': len(grade), 'pontos_descarte': len(pontos)}
