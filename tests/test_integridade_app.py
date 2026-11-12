"""Casos de falha que precisam preservar o banco e os limites de exposição."""
import uuid

import h3
import pytest
from shapely.geometry import Point, Polygon


def enviar(api, auth, **kw):
    dados = dict(lat=-8.056, lon=-34.899, categoria='entulho', volume='saco', texto='teste', chave=uuid.uuid4().hex)
    dados.update(kw)
    return api.post('/api/reportes', headers=auth, json=dados)


def test_falha_no_fim_desfaz_evento_reporte_e_pontos(api, monkeypatch):
    auth = api.entrar('cid-ana')
    db = api.servidor.E.db
    antes = {t: db.um(f'select count(*) n from {t}')['n'] for t in ('eventos','reportes','pontos','transicoes')}
    def falhar(*args, **kwargs):
        raise RuntimeError('Falha simulada ao concluir a transação')
    monkeypatch.setattr(api.servidor, 'fechar_evento', falhar)
    with pytest.raises(RuntimeError):
        enviar(api, auth)
    assert antes == {t: db.um(f'select count(*) n from {t}')['n'] for t in antes}


def test_reenvio_com_conteudo_diferente_nao_reutiliza_resultado(api):
    auth = api.entrar('cid-ana')
    chave = uuid.uuid4().hex
    assert enviar(api, auth, chave=chave).status_code == 200
    assert enviar(api, auth, chave=chave, volume='cacamba').status_code == 409
    assert enviar(api, auth, chave=chave).json()['repetido']


def test_fora_do_contorno_mesmo_dentro_de_hexagono_de_borda(api):
    auth = api.entrar('cid-ana')
    dados = api.servidor.E.dados
    candidatos = []
    for cel in dados.h3s:
        pol = Polygon([(lon,lat) for lat,lon in h3.cell_to_boundary(cel)])
        sobra = pol.difference(dados.dominio)
        if not sobra.is_empty and sobra.area > 1e-8:
            candidatos.append(sobra.representative_point())
            break
    assert candidatos
    ponto = candidatos[0]
    assert not dados.dominio.covers(Point(ponto.x,ponto.y))
    assert enviar(api,auth,lat=ponto.y,lon=ponto.x).status_code == 422
    assert api.get(f'/api/contexto?lat={ponto.y}&lon={ponto.x}',headers=auth).status_code == 422
    assert api.get('/api/contexto?lat=nan&lon=-34.9',headers=auth).status_code == 422
    assert api.get('/api/descarte?material=tipo',headers=auth).status_code == 422


def test_persistencia_apos_reabrir_banco(api):
    auth = api.entrar('cid-ana')
    enviado = enviar(api,auth).json()
    pontos = api.get('/api/eu',headers=auth).json()['pontos']
    srv = api.servidor
    srv.E.db.con.close()
    srv.E.db = None
    srv.iniciar()
    assert api.get('/api/eu',headers=auth).json()['pontos'] == pontos
    assert api.get('/api/meus-reportes',headers=auth).json()['reportes'][0]['id'] == enviado['reporte']['id']


def test_parametros_invalidos_e_rotas_por_setor(api):
    gestor = api.entrar('gestor-1','4321')
    cfg = api.get('/api/gestor/motor',headers=gestor).json()['config']
    cfg['capacidade_dia'] = 2.5
    assert api.put('/api/gestor/motor',headers=gestor,json=cfg).status_code == 422
    cfg['capacidade_dia'] = 10
    cfg['cenarios']['equilibrado']['morro']['app'] = 'NaN'
    assert api.put('/api/gestor/motor',headers=gestor,json=cfg).status_code == 422
    r = api.get('/api/gestor/rota',headers=gestor).json()
    assert r['setor'] in r['setores'] and r['paradas']
    comp = api.servidor.E.dados.comp.set_index('h3')
    assert all(str(comp.loc[p['h3'],'coleta_setor']) == r['setor'] for p in r['paradas'])
    assert api.get('/api/gestor/rota?regime=outro',headers=gestor).status_code == 422
