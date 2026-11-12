"""Ciclo completo, idempotência, transições, pontos e permissões da aplicação local."""
import uuid

# ponto dentro do Recife (Derby), longe dos exemplos simulados
LAT, LON = -8.0560, -34.8990
k = lambda: uuid.uuid4().hex


def reportar(api, h, lat=LAT, lon=LON, categoria="entulho", chave=None):
    return api.post("/api/reportes", headers=h, json={"lat": lat, "lon": lon, "categoria": categoria, "volume": "carrinho", "texto": "teste", "chave": chave or k()})


def test_ciclo_completo(api):
    ana, bruno, gestor = api.entrar("cid-ana"), api.entrar("cid-bruno"), api.entrar("gestor-1", "4321")
    r = reportar(api, ana).json()
    rid = r["reporte"]["id"]
    assert r["reporte"]["estado"] == "recebido" and r["pontos_ganhos"] in (10, 20)
    assert "score" in r["prioridade"] and r["contexto"]["bairro"]
    antes = r["prioridade"]["score"]
    # comunidade confirma -> confirmado; a prioridade da área sobe ou se mantém
    c = api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "ainda", "chave": k()}).json()
    assert c["estado"] == "confirmado" and c["pontos_ganhos"] == 5
    itens = api.get("/api/gestor/reportes", headers=gestor).json()["reportes"]
    meu = next(x for x in itens if x["id"] == rid)
    assert meu["score_da_area"] >= antes and meu["autor"] == "pseudônimo oculto"
    # gestor age
    a = api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "iniciar_atendimento", "chave": k()}).json()
    assert a["estado"] == "em_atendimento"
    a = api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "concluir_limpeza", "chave": k()}).json()
    assert a.get("estado") == "aguardando_confirmacao", a
    # cidadão acompanha e confirma o resultado
    meus = api.get("/api/meus-reportes", headers=ana).json()["reportes"]
    assert meus[0]["pode_confirmar_resultado"] and len(meus[0]["historico"]["acoes_do_gestor"]) == 2
    f = api.post(f"/api/reportes/{rid}/confirmacoes", headers=ana, json={"resposta": "limpo", "chave": k()}).json()
    assert f["estado"] == "resolvido"
    # reabertura por outro cidadão e novo atendimento
    re = api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "ainda", "chave": k()}).json()
    assert re["estado"] == "reaberto"
    assert api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "iniciar_atendimento", "chave": k()}).json()["estado"] == "em_atendimento"


def test_idempotencia_de_eventos_e_pontos(api):
    ana = api.entrar("cid-ana")
    chave = k()
    a = reportar(api, ana, chave=chave).json()
    b = reportar(api, ana, chave=chave).json()
    assert b["repetido"] and b["reporte"]["id"] == a["reporte"]["id"]
    assert len(api.get("/api/meus-reportes", headers=ana).json()["reportes"]) == 1
    assert api.get("/api/eu", headers=ana).json()["pontos"] == a["pontos_ganhos"] + sum(m["pontos"] for m in a["missoes_concluidas"])
    # mesma categoria, mesmo lugar, outra chave: vira aviso de duplicado e não pontua
    d = reportar(api, ana).json()
    assert d["duplicado"] and d["alerta_existente"]["id"] == a["reporte"]["id"]
    assert api.get("/api/eu", headers=ana).json()["pontos"] == a["pontos_ganhos"] + sum(m["pontos"] for m in a["missoes_concluidas"])
    # confirmação repetida pela mesma pessoa é recusada; mesma chave devolve o mesmo resultado
    bruno = api.entrar("cid-bruno")
    ch = k()
    rid = a["reporte"]["id"]
    c1 = api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "ainda", "chave": ch})
    c2 = api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "ainda", "chave": ch})
    assert c1.status_code == 200 and c2.json()["repetido"]
    assert api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "ainda", "chave": k()}).status_code == 409
    assert api.get("/api/eu", headers=bruno).json()["pontos"] - sum(m["pontos"] for m in c1.json()["missoes_concluidas"]) == 5


def test_transicoes_incoerentes_sao_recusadas(api):
    ana, gestor = api.entrar("cid-ana"), api.entrar("gestor-1", "4321")
    rid = reportar(api, ana).json()["reporte"]["id"]
    # concluir sem iniciar
    assert api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "concluir_limpeza", "chave": k()}).status_code == 409
    # autor não confirma o próprio alerta aberto
    assert api.post(f"/api/reportes/{rid}/confirmacoes", headers=ana, json={"resposta": "ainda", "chave": k()}).status_code == 403
    # vistoria não muda estado
    v = api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "vistoria", "chave": k()}).json()
    assert v["estado"] == "recebido"
    api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "iniciar_atendimento", "chave": k()})
    # durante o atendimento, cidadão não confirma; iniciar duas vezes é recusado
    bruno = api.entrar("cid-bruno")
    assert api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "limpo", "chave": k()}).status_code == 409
    assert api.post("/api/gestor/acoes", headers=gestor, json={"reporte_id": rid, "acao": "iniciar_atendimento", "chave": k()}).status_code == 409


def test_baixa_da_comunidade_e_perda_de_pontos(api):
    ana, bruno, carla = api.entrar("cid-ana"), api.entrar("cid-bruno"), api.entrar("cid-carla")
    r = reportar(api, ana, lat=-8.0580, lon=-34.9010).json()
    rid, base = r["reporte"]["id"], r["pontos_ganhos"] + sum(m["pontos"] for m in r["missoes_concluidas"])
    assert api.post(f"/api/reportes/{rid}/confirmacoes", headers=bruno, json={"resposta": "nao_encontrei", "chave": k()}).json()["estado"] == "recebido"
    assert api.post(f"/api/reportes/{rid}/confirmacoes", headers=carla, json={"resposta": "nao_encontrei", "chave": k()}).json()["estado"] == "resolvido"
    assert api.get("/api/eu", headers=ana).json()["pontos"] == base - r["pontos_ganhos"]


def test_permissoes_por_perfil(api):
    ana, bruno, gestor = api.entrar("cid-ana"), api.entrar("cid-bruno"), api.entrar("gestor-1", "4321")
    assert api.post("/api/sessao", json={"usuario_id": "gestor-1", "pin": "0000"}).status_code == 403
    assert api.get("/api/alertas").status_code == 401
    for rota in ("/api/gestor/fila", "/api/gestor/reportes", "/api/gestor/motor", "/api/gestor/eventos", "/api/b2b/demo", "/api/gestor/silencio"):
        assert api.get(rota, headers=ana).status_code == 403, rota
    assert reportar(api, gestor).status_code == 403
    rid = reportar(api, ana).json()["reporte"]["id"]
    # o histórico pessoal é só do autor; o alerta público não tem autor nem ponto exato
    assert api.get("/api/meus-reportes", headers=bruno).json()["reportes"] == []
    pub = next(x for x in api.get("/api/alertas", headers=bruno).json()["alertas"] if x["id"] == rid)
    assert "autor" not in pub and "lat" not in pub and abs(pub["lat_aprox"] - LAT) > 1e-6
    # cidadão recebe nível, nunca score ou motivos
    h = api.get("/api/mapa/risco", headers=ana).json()["hexagonos"][0]
    assert set(h) == {"h3", "nivel", "contorno"}
    assert "score" in api.get("/api/mapa/risco", headers=gestor).json()["hexagonos"][0]


def test_motor_editavel_e_fila_com_motivos(api):
    gestor = api.entrar("gestor-1", "4321")
    f = api.get("/api/gestor/fila", headers=gestor).json()
    assert f["filas"]["morro"] and f["filas"]["planicie"], "cenários separados para morro e planície"
    assert all(i["motivos"] for i in f["filas"]["morro"])
    cfg = api.get("/api/gestor/motor", headers=gestor).json()["config"]
    antes = f["total_acima_do_limiar"]
    cfg["limiar_fila"] = 70
    r = api.put("/api/gestor/motor", headers=gestor, json=cfg).json()
    assert r["acima_do_limiar"] < antes
    cfg["cenarios"]["equilibrado"]["morro"]["app"] = -1
    assert api.put("/api/gestor/motor", headers=gestor, json=cfg).status_code == 422
    assert api.delete("/api/gestor/motor", headers=gestor).json()["config"]["limiar_fila"] == 50
    rota = api.get("/api/gestor/rota?regime=morro", headers=gestor).json()
    assert rota["paradas"] and "LINHA RETA" in rota["metodo"]


def test_onde_descartar_e_missao(api):
    ana = api.entrar("cid-ana")
    d = api.get("/api/descarte?material=entulho", headers=ana).json()
    assert d["locais"] and all(x["situacao"] == "histórico, a confirmar" and x["data_verificacao"] == "desconhecida" for x in d["locais"])
    assert any(x["localizacao"] == "pendente" for x in d["locais"])
    ids = [x["ponto_id"] for x in d["locais"][:3]]
    ultimo = None
    for pid in ids:
        ultimo = api.post(f"/api/descarte/{pid}/confirmacoes", headers=ana, json={"resposta": "funciona", "chave": k()}).json()
        assert ultimo["pontos_ganhos"] == 5
    assert ultimo["missoes_concluidas"] and ultimo["missoes_concluidas"][0]["missao_id"] == "MS-DESCARTE-3"
    # repetir o mesmo ponto no mesmo mês não pontua de novo
    assert api.post(f"/api/descarte/{ids[0]}/confirmacoes", headers=ana, json={"resposta": "fechado", "chave": k()}).json()["pontos_ganhos"] == 0
    depois = next(x for x in api.get("/api/descarte", headers=ana).json()["locais"] if x["ponto_id"] == ids[0])
    assert "relato de participante" in depois["situacao"] and depois["relatos_do_app"] == 1


def test_exemplos_simulados_ficam_separados(api):
    gestor = api.entrar("gestor-1", "4321")
    ev = api.get("/api/gestor/eventos", headers=gestor).json()
    assert {c["origem"] for c in ev["contagem"]} == {"simulado"}
    ana = api.entrar("cid-ana")
    reportar(api, ana)
    ev = api.get("/api/gestor/eventos", headers=gestor).json()
    assert {"simulado", "uso_local"} <= {c["origem"] for c in ev["contagem"]}
    assert all(x["exemplo_simulado"] for x in api.get("/api/alertas", headers=ana).json()["alertas"] if x["id"] not in
               [m["id"] for m in api.get("/api/meus-reportes", headers=ana).json()["reportes"]])
    rk = api.get("/api/ranking", headers=ana).json()
    assert "PARTICIPAÇÃO" in rk["explicacao"] and all(set(b) == {"bairro", "participacoes"} for b in rk["bairros_por_participacao"])
    b2b = api.get("/api/b2b/demo", headers=gestor).json()
    assert "DEMONSTRAÇÃO" in b2b["rotulo"] and any("MTR" in a for a in b2b["avisos"])
