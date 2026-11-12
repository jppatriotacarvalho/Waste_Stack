"""Protótipo "Recife em Dados" (design da equipe): servido localmente e com números iguais aos do pipeline.

Os números estão escritos no próprio design (app/static/recife/index.html e v1.html). Este teste formata os
valores de data/gold/dados_publicos_resumo.json (notebook 28) e exige que cada um apareça no design. Se os
dados mudarem e o design não, o teste falha: é o aviso para atualizar o protótipo.
"""
import re
from pathlib import Path

RECIFE = Path(__file__).resolve().parents[1] / "app" / "static" / "recife"


def br(n, casas=0):
    """Número no formato brasileiro (1.488.920; 93,4)."""
    s = f"{n:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def esperados_v2(t):
    c, m, co, d, f, cb = (t[k] for k in ("central156", "morros", "coleta", "descarte", "feiras", "cobertura"))
    terr = t["territorio"]
    fila = c["fila_drenagem"]
    mat = {x["rotulo"]: x["valor"] for x in d["materiais"]}
    return [
        f"{terr['bairros']} bairros", f"{br(terr['populacao'])} moradores", f"{br(terr['hexagonos'])} hexágonos",
        br(c["recorte"]), br(c["total"]), f"{br(100 * c['recorte'] / c['total'], 1)}%", f"{br(c['pct_recorrencia'], 1)}%",
        br(fila["abertos"] + fila["parados"]), br(fila["abertos"]), br(fila["parados"]),
        f"R$ {br(f['limpeza_valor'] / 1e6, 1)} mi", f"R$ {br(f['limpeza_valor'] / 1e8, 1)} mi",
        br(c["por_categoria"]["lixo"]), br(c["por_categoria"]["vias"]), br(c["por_categoria"]["drenagem"]),
        f"{c['mediana_dias']['lixo']:.0f} dias", f"{c['mediana_dias']['vias']:.0f} dias", f"{c['mediana_dias']['drenagem']:.0f} dias",
        f"{round(c['mediana_dias']['drenagem'] / c['mediana_dias']['lixo'])}×", f"{c['ruas_20_meses']} ruas",
        f"{terr['plano']['Sem galeria registrada']:.0f}%", f"RPA {co['rpa_sem_drenagem']['rpa']}", f"{co['rpa_sem_drenagem']['pct']:.0f}%",
        f"{br(co['pct_dia_sem_coleta'], 1)}%", f"{br(co['pct_sem_seletiva'], 1)}%", f"{co['cis_deficit']} / {co['cis']}",
        f"{terr['plano']['Limpeza mensal ou nenhuma']:.0f}%", str(d["desertos"]),
        f"{m['pct_morro']:.0f}%", f"{round(m['acoes'] / 1000)} mil", str(m["ruas_r4"]), f"{br(m['lona_m2'] / 1e6, 1)} mi m²",
        f"{mat['Recicláveis']} locais", str(mat["Entulho"]), str(mat["Pilhas"]), str(mat["Lâmpadas"]), str(mat["Medicamentos"]),
        f"De {d['locais']} locais", f"{br(cb['teste']['ruas'], 1)}%", f"{br(cb['teste']['score'], 1)}%", br(cb["correlacao"], 2),
        f"{cb['zonas']} áreas", f"{m['alagamento_registros']} vezes, em {m['alagamento_dias']} dias",
        f"{d['ecoestacoes_sem_coordenada']} das {d['ecoestacoes']}",
    ]


def texto(arq):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (RECIFE / arq).read_text(encoding="utf-8")))


def test_numeros_do_prototipo_conferem_com_o_pipeline(api):
    t = api.get("/api/dados-publicos?perfil=gestor").json()["temas"]
    pagina = texto("index.html")
    faltando = [e for e in esperados_v2(t) if e not in pagina]
    assert not faltando, f"números do design diferentes dos dados (atualize o protótipo): {faltando}"


def test_versao_com_abas_tambem_confere(api):
    t = api.get("/api/dados-publicos?perfil=gestor").json()["temas"]
    pagina = texto("v1.html")
    c, terr = t["central156"], t["territorio"]
    for e in [br(terr["populacao"]), br(terr["hexagonos"]), f"{br(terr['pct_sobreposicao'], 1)}%", f"{terr['rios_rua']} “ruas”",
              br(c["total"]), br(c["recorte"]), f"{br(c['pct_mapeados'], 1)}%", f"{br(c['pct_recorrencia'], 1)}%",
              f"{br(t['agua']['pct_area_faixa'], 0)}%", f"{br(t['coleta']['drenagem_km'])} km", f"{br(t['coleta']['esgoto_km'])} km",
              str(t["feiras"]["feiras"]), str(t["feiras"]["mercados"])]:
        assert e in pagina, e


def test_prototipo_servido_e_sem_dependencia_externa(api):
    r = api.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/recife/"
    assert "Recife em Dados" in api.get("/recife/").text
    assert api.get("/recife/v1.html").status_code == 200
    for arq in ("support.js", "vendor/react.production.min.js", "vendor/react-dom.production.min.js",
                "_ds/organic-54d87b73-db22-44c3-860e-abe238760f8a/styles.css",
                "_ds/organic-54d87b73-db22-44c3-860e-abe238760f8a/fontes/caprasimo-latin-400-normal.woff2"):
        assert api.get(f"/recife/{arq}").status_code == 200, arq
    for arq in ("index.html", "v1.html"):
        h = (RECIFE / arq).read_text(encoding="utf-8")
        assert "fonts.googleapis" not in h and "window.__resources" in h
    css = next(RECIFE.glob("_ds/*/styles.css")).read_text(encoding="utf-8")
    assert "googleapis" not in css and "@font-face" in css


def test_resumo_por_perfil_sem_dados_pessoais(api):
    cid = api.get("/api/dados-publicos").json()
    ges = api.get("/api/dados-publicos?perfil=gestor").json()
    assert all("fonte_tecnica" not in t for t in cid["temas"].values())
    assert all("fonte_tecnica" in t for t in ges["temas"].values())
    assert all(t["fonte"] and t["periodo"] for t in cid["temas"].values())
    # só agregados: nenhuma rua do 156 aparece no resumo
    reinc = api.servidor.pd.read_parquet(api.servidor.GOLD / "base156_reincidencia_logradouro.parquet")
    texto_resumo = str(cid).lower()
    assert not any(r.lower() in texto_resumo for r in reinc.logradouro_norm.head(300) if len(r) > 12)
