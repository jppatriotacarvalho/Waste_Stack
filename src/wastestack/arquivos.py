"""Leitura conservadora, proveniência e escrita das camadas locais."""
import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .bronze import inspecionar_arquivo
from .config import BRONZE, RAIZ, SILVER, GOLD, EPSG_METROS
from .limpeza import normalizar_nome


def agora():
    return datetime.now(timezone.utc).isoformat()


def json_salvar(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str, allow_nan=False) + '\n', encoding='utf-8')


def fonte(nome):
    candidatos = sorted(p for p in BRONZE.rglob(nome) if '_restrito' not in p.parts)
    if len(candidatos) != 1:
        raise ValueError(f'Esperada uma fonte inequívoca: {nome} ({len(candidatos)})')
    return candidatos[0]


def csv_ler(path):
    path = Path(path)
    info = inspecionar_arquivo(path)
    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as z:
            data = io.BytesIO(z.read(info['membro_csv']))
    else:
        data = path
    frame = pd.read_csv(data, sep=info['separador'], encoding=info['codificacao'], dtype=str, keep_default_na=False)
    frame.columns = [c.strip() for c in frame.columns]
    return frame.apply(lambda s: s.str.replace('\u00a0', ' ').str.strip())


def numero(s):
    return pd.to_numeric(s.astype(str).str.replace(',', '.', regex=False), errors='coerce')


def codigo(s):
    return numero(s).round().astype('Int64').astype('string')


def geo_ler(nome):
    g = gpd.read_file(fonte(nome))
    if g.crs is None:
        # GeoJSON RFC 7946: longitude/latitude. Conferir domínio antes de atribuir.
        bounds = g.total_bounds
        if not (-36 < bounds[0] < -33 and -10 < bounds[1] < -6):
            raise ValueError(f'CRS ausente e coordenadas inesperadas: {nome}')
        g = g.set_crs(4326)
    return g.to_crs(EPSG_METROS)


def salvar(frame, nome, camada=SILVER, fontes=None, nota=''):
    camada.mkdir(parents=True, exist_ok=True)
    path = camada / f'{nome}.parquet'
    if isinstance(frame, gpd.GeoDataFrame):
        frame = frame.to_crs(4326)
    frame.to_parquet(path, index=False)
    check = gpd.read_parquet(path) if isinstance(frame, gpd.GeoDataFrame) else pd.read_parquet(path)
    if len(check) != len(frame) or list(check.columns) != list(frame.columns):
        raise AssertionError(f'Falha de releitura: {nome}')
    json_salvar(path.with_suffix('.meta.json'), {'tabela': nome, 'gerada_em': agora(), 'registros':len(frame),
        'fontes':fontes or [], 'nota':nota, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'uso':'local; publicação exige as regras de agregação', 'crs':str(getattr(frame,'crs','não espacial'))})
    return frame


def ler(nome, camada=SILVER, geo=False):
    path=camada / f'{nome}.parquet'
    return gpd.read_parquet(path).to_crs(EPSG_METROS) if geo else pd.read_parquet(path)


def conferir_bronze():
    catalogo = list(csv.DictReader((BRONZE / '_catalogo_bronze.csv').open(encoding='utf-8-sig')))
    resultado=[]
    for row in catalogo:
        info=inspecionar_arquivo(RAIZ / row['arquivo'])
        for key in ('md5','bytes','linhas_de_dados','colunas'):
            if str(info[key]) != row[key]:
                raise ValueError(f'Integridade divergente: {row["arquivo"]}: {key}')
        # Esquema, nunca valores pessoais nos relatórios.
        resultado.append({**row, 'verificacao':'confere', 'codificacao':info['codificacao'],
            'campos':info['campos'], 'sha256':info['sha256'], 'periodo_referencia':'consultar manifesto de recebimento'})
    json_salvar(RAIZ/'data/validacao/bronze.json', {'verificado_em':agora(),'entradas':resultado})
    return {'entradas':len(resultado),'integridade':'aprovada'}


def bairro_pontos(pontos, bairros):
    """Não escolhe silenciosamente um bairro quando um ponto toca mais de um."""
    out = pontos.copy()
    joined = gpd.sjoin(out[['geometry']], bairros[['bairro_codigo','geometry']], how='left', predicate='intersects')
    groups = joined.groupby(level=0)['bairro_codigo'].agg(lambda s: sorted(set(s.dropna().astype(str))))
    out['bairros_candidatos'] = out.index.map(lambda i: json.dumps(groups.get(i,[])))
    out['bairro_codigo'] = out.index.map(lambda i: groups[i][0] if len(groups.get(i,[]))==1 else None)
    out['bairro_ambiguo'] = out.index.map(lambda i:len(groups.get(i,[]))>1)
    return out
