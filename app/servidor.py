"""API e interface locais do WasteStack (demonstração; não publicar).

    python -m app.servidor            # http://127.0.0.1:8156

Persistência: SQLite em data/operacional/ (fora do Git). Dados de partida: tabelas Gold em data/gold/.
Dois caminhos de dados, mantidos separados:
  1. fontes públicas em LOTE (pipeline Bronze → Silver → Gold), lidas aqui só na partida;
  2. EVENTOS do app e do gestor, gravados um a um quando chegam (não é streaming).
"""
if __name__ == "__main__":
    print("WasteStack: carregando bibliotecas (na primeira vez no Windows/OneDrive pode levar alguns minutos; não interrompa)...", flush=True)

import contextlib
import json
import math
import os
import secrets
import sqlite3
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from shapely.geometry import Point

import h3
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
from wastestack import motor  # noqa: E402
from wastestack.dados_publicos import para_perfil  # noqa: E402

from . import regras  # noqa: E402
from .regras import RegraViolada  # noqa: E402

GOLD = Path(os.environ.get("WASTESTACK_GOLD", RAIZ / "data" / "gold"))
OPERACIONAL = Path(os.environ.get("WASTESTACK_OPERACIONAL", RAIZ / "data" / "operacional"))
ESTATICO = Path(__file__).parent / "static"
H3_RES = 9
TRAVA = threading.RLock()


def agora():
    return datetime.now(timezone.utc)


def iso(d=None):
    return (d or agora()).isoformat(timespec="seconds")


def limpo(v):
    """Converte valores do pandas em JSON simples (NaN -> None)."""
    if isinstance(v, dict):
        return {k: limpo(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [limpo(x) for x in v]
    if hasattr(v, "tolist") and not isinstance(v, str):
        return limpo(v.tolist())
    if v is None or v is pd.NA or v is pd.NaT:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        return limpo(v.item())
    return v


def registros(df):
    return limpo(df.to_dict("records"))


# ------------------------------------------------------------------ dados Gold (lote)
class Dados:
    def __init__(self):
        ler = lambda n: pd.read_parquet(GOLD / f"{n}.parquet")
        self.comp = ler("motor_componentes_h3")
        self.h3s = set(self.comp.h3)
        self.ctx = ler("app_contexto_local").set_index("h3")
        self.descarte = ler("app_onde_descartar")
        self.missoes = ler("app_missoes")
        self.exemplos = ler("app_exemplos_simulados")
        self.silencio = ler("motor_esperado_registrado_h3")
        self.rotulos = {}
        for nome in ("mercado_orcamento_zeladoria", "base156_desempenho_mensal", "base156_fila_aberta", "motor_teste_partida",
                     "motor_auditoria_quartil", "motor_comparacao_esforco", "painel_agua_residuo", "painel_drenagem", "silencio_bairro", "calendario_polos_comercio"):
            self.rotulos[nome] = ler(nome)
        import geopandas as gpd
        b = gpd.read_parquet(GOLD / "mapa_base_bairros.parquet").to_crs(4326)
        self.bairros_geojson = json.loads(b[["bairro_codigo", "bairro_nome", "rpa", "geometry"]].to_json())
        vias = gpd.read_parquet(GOLD / 'mapa_base_vias.parquet').to_crs(4326)
        self.vias_geojson = json.loads(vias[['logradouro_norm', 'geometry']].to_json())
        self.dominio = gpd.read_parquet(GOLD / 'dominio_demonstracao.parquet').to_crs(4326).geometry.iloc[0]
        f = gpd.read_parquet(GOLD / "camada_faixas_marginais.parquet").to_crs(4326)
        self.faixas_geojson = json.loads(f[["nome", "classe", "geometry"]].to_json())
        self.contorno = {h: [[lat, lon] for lat, lon in h3.cell_to_boundary(h)] for h in self.comp.h3[self.comp.via_m > 0]}


# ------------------------------------------------------------------ banco operacional (eventos)
ESQUEMA = """
create table if not exists usuarios(id text primary key, apelido text, perfil text, origem text);
create table if not exists sessoes(token text primary key, usuario_id text, criada_em text);
create table if not exists eventos(id text primary key, tipo text, usuario_id text, origem text, criado_em text,
    chave_idempotencia text unique, ref_id text, payload text, resultado text);
create table if not exists reportes(id text primary key, autor_id text, categoria text, volume text, lat real, lon real, h3 text,
    bairro_nome text, texto text, estado text, ciclo integer default 1, origem text, criado_em text, atualizado_em text,
    beira_de_rio integer, zona_silencio integer);
create table if not exists transicoes(id integer primary key autoincrement, reporte_id text, de text, para text, motivo text, evento_id text, em text);
create table if not exists confirmacoes(id integer primary key autoincrement, reporte_id text, usuario_id text, resposta text, ciclo integer,
    fase text, evento_id text, em text, unique(reporte_id, usuario_id, ciclo, fase));
create table if not exists acoes_gestor(id integer primary key autoincrement, reporte_id text, usuario_id text, orgao text, acao text, nota text, evento_id text, em text);
create table if not exists descarte_confirmacoes(id integer primary key autoincrement, ponto_id text, usuario_id text, resposta text, material text, evento_id text, em text);
create table if not exists missoes_concluidas(missao_id text, usuario_id text, evento_id text, em text, primary key(missao_id, usuario_id));
create table if not exists pontos(id integer primary key autoincrement, usuario_id text, regra text, ref text, pontos integer, evento_id text, em text,
    unique(usuario_id, regra, ref));
create table if not exists config(chave text primary key, valor text);
"""


class Banco:
    def __init__(self, caminho):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(caminho, check_same_thread=False, isolation_level=None)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(ESQUEMA)

    def q(self, sql, args=()):
        with TRAVA:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def um(self, sql, args=()):
        with TRAVA:
            r = self.con.execute(sql, args).fetchone()
            return dict(r) if r else None

    def x(self, sql, args=()):
        with TRAVA:
            return self.con.execute(sql, args)

    @contextlib.contextmanager
    def transacao(self):
        """Evento, estado e pontos são confirmados juntos, ou todos são desfeitos."""
        with TRAVA:
            self.con.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.con.execute('COMMIT')
            except Exception:
                self.con.execute('ROLLBACK')
                raise


class Estado:
    dados: Dados = None
    db: Banco = None
    pin_gestor: str = ""
    resumo: dict = None


E = Estado()


def distancia_m(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ------------------------------------------------------------------ motor em uso
def config_motor():
    linha = E.db.um("select valor from config where chave='motor'")
    return motor.validar_config(json.loads(linha["valor"])) if linha else motor.carregar_config()


def reportes_ativos_df():
    linhas = E.db.q("""select r.h3, r.estado, r.volume, (select count(*) from confirmacoes c where c.reporte_id=r.id and c.ciclo=r.ciclo and c.resposta='ainda') as confirmacoes
                       from reportes r""")
    return pd.DataFrame(linhas, columns=["h3", "estado", "volume", "confirmacoes"])


def scores():
    """Recalcula a prioridade com os parâmetros atuais e os reportes ativos. Chamado a cada leitura: sempre atual."""
    cfg = config_motor()
    return motor.calcular_score(E.dados.comp, cfg, app=motor.componente_app(reportes_ativos_df())), cfg


# ------------------------------------------------------------------ eventos, idempotência e pontos
def evento_repetido(chave, usuario_id, tipo=None, ref_id=None, payload=None):
    if not chave or len(chave) < 8:
        raise HTTPException(422, "Envie uma chave de idempotência (campo 'chave', 8+ caracteres) em cada ação.")
    ev = E.db.um("select * from eventos where chave_idempotencia=?", (chave,))
    if ev is None:
        return None
    if ev["usuario_id"] != usuario_id:
        raise HTTPException(409, "Chave de idempotência já usada por outro participante.")
    anterior = json.loads(ev['payload'])
    if ((tipo is not None and ev['tipo'] != tipo) or (ref_id is not None and ev['ref_id'] != ref_id)
            or (payload is not None and any(anterior.get(k) != v for k, v in payload.items()))):
        raise HTTPException(409, 'Esta chave já identifica outro envio. Use uma nova chave para uma ação diferente.')
    return dict(json.loads(ev["resultado"] or "{}"), repetido=True)


def gravar_evento(tipo, usuario, chave, ref_id, payload, origem=None):
    eid = "ev-" + uuid.uuid4().hex[:16]
    E.db.x("insert into eventos(id,tipo,usuario_id,origem,criado_em,chave_idempotencia,ref_id,payload) values(?,?,?,?,?,?,?,?)",
           (eid, tipo, usuario["id"], origem or usuario["origem"], iso(), chave, ref_id, json.dumps(payload, ensure_ascii=False)))
    return eid


def fechar_evento(eid, resultado):
    E.db.x("update eventos set resultado=? where id=?", (json.dumps(resultado, ensure_ascii=False), eid))
    return resultado


def pontuar(usuario_id, regra, ref, pts, eid):
    """Lança pontos uma única vez por (participante, regra, referência). Devolve os pontos realmente lançados."""
    if pts == 0:
        return 0
    cur = E.db.x("insert or ignore into pontos(usuario_id,regra,ref,pontos,evento_id,em) values(?,?,?,?,?,?)", (usuario_id, regra, ref, pts, eid, iso()))
    return pts if cur.rowcount else 0


def transicionar(reporte, gatilho, eid, motivo):
    novo = regras.proximo_estado(reporte["estado"], gatilho)
    ciclo = reporte["ciclo"] + (1 if novo == "reaberto" else 0)
    E.db.x("update reportes set estado=?, ciclo=?, atualizado_em=? where id=?", (novo, ciclo, iso(), reporte["id"]))
    E.db.x("insert into transicoes(reporte_id,de,para,motivo,evento_id,em) values(?,?,?,?,?,?)", (reporte["id"], reporte["estado"], novo, motivo, eid, iso()))
    return novo


def concluir_missao(usuario, missao_id, origem):
    m = E.dados.missoes[E.dados.missoes.missao_id == missao_id]
    if m.empty or E.db.um("select 1 from missoes_concluidas where missao_id=? and usuario_id=?", (missao_id, usuario["id"])):
        return None
    eid = gravar_evento("missao_concluida", usuario, "missao:" + missao_id + ":" + usuario["id"], missao_id, {"missao_id": missao_id}, origem)
    E.db.x("insert into missoes_concluidas values(?,?,?,?)", (missao_id, usuario["id"], eid, iso()))
    pts = pontuar(usuario["id"], "missao", missao_id, int(m.pontos.iloc[0]), eid)
    return fechar_evento(eid, {"missao_id": missao_id, "titulo": m.titulo.iloc[0], "pontos": pts})


def missoes_por_area(usuario, h3id, origem):
    alvo = "MS-" + h3id
    return [r for r in [concluir_missao(usuario, alvo, origem)] if r]


def total_pontos(uid):
    return E.db.um("select coalesce(sum(pontos),0) t from pontos where usuario_id=?", (uid,))["t"]


# ------------------------------------------------------------------ visões por perfil
def visto_ha(texto_iso):
    seg = (agora() - datetime.fromisoformat(texto_iso)).total_seconds()
    if seg < 3600:
        return f"há {max(1, int(seg // 60))} min"
    if seg < 86400:
        return f"há {int(seg // 3600)} h"
    return f"há {int(seg // 86400)} dias"


def confirmacoes_de(rid):
    c = E.db.q("select resposta, count(*) n from confirmacoes where reporte_id=? group by resposta", (rid,))
    return {x["resposta"]: x["n"] for x in c}


def alerta_publico(r):
    """O que qualquer pessoa vê: posição no centro do hexágono, sem autor e sem ponto exato."""
    lat, lon = h3.cell_to_latlng(r["h3"])
    return {"id": r["id"], "categoria": r["categoria"], "volume": regras.VOLUMES.get(r["volume"], r["volume"]), "estado": r["estado"],
            "estado_rotulo": regras.ROTULO_ESTADO[r["estado"]], "bairro": r["bairro_nome"], "lat_aprox": lat, "lon_aprox": lon, "h3": r["h3"],
            "visto": visto_ha(r["atualizado_em"]), "confirmacoes": confirmacoes_de(r["id"]), "exemplo_simulado": r["origem"] == "simulado",
            "beira_de_rio": bool(r["beira_de_rio"]), "posicao": "aproximada (centro do hexágono de ~200 m)"}


def historico(rid):
    t = E.db.q("select de, para, motivo, em from transicoes where reporte_id=? order by id", (rid,))
    a = E.db.q("select acao, orgao, nota, em from acoes_gestor where reporte_id=? order by id", (rid,))
    for x in t:
        x["de"], x["para"] = regras.ROTULO_ESTADO.get(x["de"], x["de"]), regras.ROTULO_ESTADO.get(x["para"], x["para"])
    for x in a:
        x["acao"] = regras.ACOES_GESTOR.get(x["acao"], x["acao"])
    return {"transicoes": t, "acoes_do_gestor": a}


# ------------------------------------------------------------------ sementes
def semear():
    if E.db.um("select 1 from usuarios limit 1"):
        return
    usuarios = [("cid-ana", "Ana", "cidadao", "uso_local"), ("cid-bruno", "Bruno", "cidadao", "uso_local"), ("cid-carla", "Carla", "cidadao", "uso_local"),
                ("gestor-1", "Gestor (demonstração)", "gestor", "uso_local"),
                ("sim-1", "Participante simulado 1", "cidadao", "simulado"), ("sim-2", "Participante simulado 2", "cidadao", "simulado"),
                ("sim-3", "Participante simulado 3", "cidadao", "simulado")]
    E.db.con.executemany("insert into usuarios values(?,?,?,?)", usuarios)
    sims = [E.db.um("select * from usuarios where id=?", (f"sim-{i}",)) for i in (1, 2, 3)]
    for i, ex in enumerate(E.dados.exemplos.to_dict("records")):
        autor = sims[i % 3]
        res = criar_reporte(autor, ex["lat"], ex["lon"], ex["categoria"], ex["volume"], ex["texto"], "semente:" + ex["exemplo_id"], origem="simulado", sem_limite=True)
        for k in range(int(ex["confirmacoes_simuladas"])):
            outro = sims[(i + 1 + k) % 3]
            confirmar(outro, res["reporte"]["id"], "ainda", f"semente:{ex['exemplo_id']}:c{k}", origem="simulado")


def credenciais():
    arq = OPERACIONAL / "credenciais_demo.json"
    if arq.exists():
        return json.loads(arq.read_text(encoding="utf-8"))["pin_gestor"]
    pin = os.environ.get("WASTESTACK_PIN_GESTOR") or f"{secrets.randbelow(10000):04d}"
    arq.write_text(json.dumps({"pin_gestor": pin, "aviso": "PIN local de demonstração; não é controle de acesso de produção"}, ensure_ascii=False, indent=2), encoding="utf-8")
    return pin


def iniciar():
    OPERACIONAL.mkdir(parents=True, exist_ok=True)
    E.dados = Dados()
    E.db = Banco(OPERACIONAL / "wastestack.db")
    E.pin_gestor = credenciais()
    arq = GOLD / "dados_publicos_resumo.json"
    E.resumo = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else None
    with E.db.transacao():
        semear()


# ------------------------------------------------------------------ operações de domínio
def criar_reporte(usuario, lat, lon, categoria, volume, texto, chave, origem=None, sem_limite=False):
    rep = evento_repetido(chave, usuario["id"], 'reporte_criado', payload={'lat': lat, 'lon': lon, 'categoria': categoria, 'volume': volume, 'texto': texto})
    if rep:
        return rep
    if categoria not in regras.CATEGORIAS or volume not in regras.VOLUMES:
        raise HTTPException(422, "Categoria ou volume inválido.")
    cel = h3.latlng_to_cell(lat, lon, H3_RES)
    if cel not in E.dados.h3s or not E.dados.dominio.covers(Point(lon, lat)):
        raise HTTPException(422, "O local fica fora da área coberta (Recife).")
    limite = iso(agora() - timedelta(hours=regras.DEDUP_HORAS))
    for r in E.db.q("select * from reportes where categoria=? and estado not in ('resolvido') and criado_em>=?", (categoria, limite)):
        if distancia_m(lat, lon, r["lat"], r["lon"]) <= regras.DEDUP_METROS:
            return {"duplicado": True, "mensagem": "Já há um alerta desta categoria aqui. Confirme em vez de criar outro.", "alerta_existente": alerta_publico(r)}
    if not sem_limite:
        uma_hora = iso(agora() - timedelta(hours=1))
        n = E.db.um("select count(*) n from reportes where autor_id=? and criado_em>=?", (usuario["id"], uma_hora))["n"]
        if n >= regras.LIMITE_REPORTES_HORA:
            raise HTTPException(429, f"Limite de {regras.LIMITE_REPORTES_HORA} reportes por hora atingido.")
    ctx = E.dados.ctx.loc[cel]
    rid = "rp-" + uuid.uuid4().hex[:12]
    eid = gravar_evento("reporte_criado", usuario, chave, rid, {"lat": lat, "lon": lon, "categoria": categoria, "volume": volume, "texto": texto, "foto": None}, origem)
    beira, silencio = bool(ctx.beira_de_rio), bool(ctx.zona_silencio)
    E.db.x("insert into reportes(id,autor_id,categoria,volume,lat,lon,h3,bairro_nome,texto,estado,ciclo,origem,criado_em,atualizado_em,beira_de_rio,zona_silencio) "
           "values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (rid, usuario["id"], categoria, volume, lat, lon, cel, limpo(ctx.bairro_nome), (texto or "")[:280], "recebido", 1, origem or usuario["origem"], iso(), iso(), int(beira), int(silencio)))
    E.db.x("insert into transicoes(reporte_id,de,para,motivo,evento_id,em) values(?,?,?,?,?,?)", (rid, "", "recebido", "reporte criado", eid, iso()))
    pts = pontuar(usuario["id"], "reporte", rid, regras.PONTOS["reporte"] * (regras.BONUS_AREA if (beira or silencio) else 1), eid)
    missoes = missoes_por_area(usuario, cel, origem)
    s, cfg = scores()
    linha = s[s.h3 == cel].iloc[0]
    r = E.db.um("select * from reportes where id=?", (rid,))
    return fechar_evento(eid, {
        "duplicado": False, "reporte": {"id": rid, "estado": "recebido", "estado_rotulo": regras.ROTULO_ESTADO["recebido"], **alerta_publico(r)},
        "contexto": contexto_do_hex(cel), "pontos_ganhos": pts, "bonus": "beira de rio" if beira else ("zona de silêncio" if silencio else None),
        "missoes_concluidas": missoes,
        "prioridade": {"score": float(linha.score), "faixa": linha.faixa, "na_fila": bool(linha.em_fila), "motivos": list(linha.motivos)}})


def confirmar(usuario, rid, resposta, chave, origem=None):
    rep = evento_repetido(chave, usuario["id"], 'reporte_confirmado', rid, {'resposta': resposta})
    if rep:
        return rep
    r = E.db.um("select * from reportes where id=?", (rid,))
    if not r:
        raise HTTPException(404, "Reporte não encontrado.")
    eh_autor = r["autor_id"] == usuario["id"]
    fase = {"aguardando_confirmacao": "resultado", "resolvido": "reabertura"}.get(r["estado"], "aberto")
    if r["estado"] == "resolvido" and datetime.fromisoformat(r["atualizado_em"]) < agora() - timedelta(days=regras.DIAS_PARA_REABRIR):
        raise HTTPException(409, "Prazo para reabrir encerrado; crie um novo reporte.")
    if E.db.um("select 1 from confirmacoes where reporte_id=? and usuario_id=? and ciclo=? and fase=?", (rid, usuario["id"], r["ciclo"], fase)):
        raise HTTPException(409, "Você já respondeu sobre este alerta nesta etapa.")
    baixas = E.db.um("select count(distinct usuario_id) n from confirmacoes where reporte_id=? and ciclo=? and fase='aberto' and resposta in ('limpo','nao_encontrei') and usuario_id<>?",
                     (rid, r["ciclo"], r["autor_id"]))["n"]
    try:
        gatilho = regras.gatilho_da_confirmacao(r["estado"], resposta, eh_autor, baixas)
    except RegraViolada as e:
        raise HTTPException(e.codigo, e.mensagem)
    sem_conf_antes = E.db.um("select count(*) n from confirmacoes where reporte_id=?", (rid,))["n"] == 0
    eid = gravar_evento("reporte_confirmado", usuario, chave, rid, {"resposta": resposta, "fase": fase, "foto": None}, origem)
    E.db.x("insert into confirmacoes(reporte_id,usuario_id,resposta,ciclo,fase,evento_id,em) values(?,?,?,?,?,?,?)", (rid, usuario["id"], resposta, r["ciclo"], fase, eid, iso()))
    novo = transicionar(r, gatilho, eid, f"cidadão respondeu: {regras.RESPOSTAS[resposta]}") if gatilho else r["estado"]
    if not gatilho:
        E.db.x("update reportes set atualizado_em=? where id=?", (iso(), rid))
    pts = pontuar(usuario["id"], "confirmacao", f"{rid}:{r['ciclo']}:{fase}", regras.PONTOS["confirmacao"], eid)
    if resposta == "ainda" and not eh_autor:
        pontuar(r["autor_id"], "reporte_confirmado", rid, regras.PONTOS["reporte_confirmado"], eid)
    if gatilho == "baixa_comunidade":
        nao = E.db.um("select count(distinct usuario_id) n from confirmacoes where reporte_id=? and resposta='nao_encontrei'", (rid,))["n"]
        if nao >= regras.BAIXAS_PARA_ENCERRAR:
            ganho = E.db.um("select coalesce(sum(pontos),0) t from pontos where usuario_id=? and regra='reporte' and ref=?", (r["autor_id"], rid))["t"]
            pontuar(r["autor_id"], "reporte_nao_encontrado", rid, -ganho, eid)
    missoes = missoes_por_area(usuario, r["h3"], origem)
    if sem_conf_antes:
        m = concluir_missao(usuario, "MS-ALERTA-SEM-CONF", origem)
        missoes += [m] if m else []
    return fechar_evento(eid, {"reporte_id": rid, "estado": novo, "estado_rotulo": regras.ROTULO_ESTADO[novo], "mudou_de_estado": novo != r["estado"],
                               "pontos_ganhos": pts, "missoes_concluidas": missoes})


def contexto_do_hex(cel):
    c = E.dados.ctx.loc[cel]
    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    coleta = None
    if isinstance(c.coleta_frequencia, str):
        nomes = [dias[int(d)] for d in str(c.coleta_dias).split(",") if d != ""]
        hoje = agora().astimezone(timezone(timedelta(hours=-3))).weekday()
        coleta = {"frequencia": c.coleta_frequencia, "turno": c.coleta_turno, "dias": nomes, "hoje_tem_coleta": dias[hoje] in nomes,
                  "nota": "plano da fonte Núcleos urbanizados; vigência a confirmar com a Emlurb"}
    p = E.dados.descarte[E.dados.descarte.ponto_id == c.descarte_mais_proximo]
    return limpo({"bairro": c.bairro_nome, "coleta_regular": coleta,
                  "coleta_seletiva": {"dia": c.seletiva_dia, "turno": c.seletiva_turno} if isinstance(c.seletiva_dia, str) else None,
                  "nivel_reincidencia": c.nivel_reincidencia, "nota_reincidencia": c.nota_reincidencia, "beira_de_rio": bool(c.beira_de_rio),
                  "zona_de_silencio": bool(c.zona_silencio),
                  "descarte_mais_proximo": {"nome": p.nome.iloc[0], "tipo": p.tipo.iloc[0], "distancia_m": c.dist_descarte_m, "situacao": p.situacao.iloc[0],
                                            "aviso": "distância em linha reta; lista histórica, a confirmar"} if len(p) else None,
                  "fontes": c.fonte_dado})


# ------------------------------------------------------------------ API
@contextlib.asynccontextmanager
async def _vida(_app):
    if E.db is None:
        iniciar()
    yield


app = FastAPI(title="WasteStack — demonstração local", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=_vida)


def usuario_atual(x_sessao: str = Header(default="")):
    s = E.db.um("select u.* from sessoes s join usuarios u on u.id=s.usuario_id where s.token=?", (x_sessao,))
    if not s:
        raise HTTPException(401, "Entre com um perfil para continuar.")
    return s


def cidadao(u=Depends(usuario_atual)):
    if u["perfil"] != "cidadao":
        raise HTTPException(403, "Ação disponível só para o perfil cidadão.")
    return u


def gestor(u=Depends(usuario_atual)):
    if u["perfil"] != "gestor":
        raise HTTPException(403, "Área restrita ao perfil gestor.")
    return u


class Entrar(BaseModel):
    usuario_id: str
    pin: str = ""


class NovoReporte(BaseModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    categoria: str
    volume: str
    texto: str = Field(default="", max_length=280)
    chave: str


class Resposta(BaseModel):
    resposta: str
    chave: str
    material: str = ""


class Acao(BaseModel):
    reporte_id: str
    acao: str
    orgao: str = "Emlurb"
    nota: str = Field(default="", max_length=280)
    chave: str


@app.get("/api/saude")
def saude():
    return {"ok": True, "demonstracao_local": True, "hexagonos": len(E.dados.comp), "eventos": E.db.um("select count(*) n from eventos")["n"]}


@app.get("/api/perfis")
def perfis():
    u = E.db.q("select id, apelido, perfil from usuarios where origem='uso_local' order by perfil, apelido")
    return {"perfis": u, "aviso": "Perfis de demonstração. O gestor pede o PIN local gravado em data/operacional/credenciais_demo.json."}


@app.post("/api/sessao")
def entrar(e: Entrar):
    u = E.db.um("select * from usuarios where id=? and origem='uso_local'", (e.usuario_id,))
    if not u:
        raise HTTPException(404, "Perfil não encontrado.")
    if u["perfil"] == "gestor" and not secrets.compare_digest(e.pin, E.pin_gestor):
        raise HTTPException(403, "PIN do gestor incorreto.")
    token = secrets.token_urlsafe(24)
    E.db.x("insert into sessoes values(?,?,?)", (token, u["id"], iso()))
    return {"token": token, "usuario": {"id": u["id"], "apelido": u["apelido"], "perfil": u["perfil"]}}


@app.get("/api/dados-publicos")
def dados_publicos(perfil: str = "cidadao"):
    """Números da página Recife em Dados, gerados pelo notebook 28 (só agregados; a referência técnica vai só ao gestor).

    O protótipo visual (app/static/recife/) é o design aprovado pela equipe; `tests/test_prototipo.py` confere que
    cada número escrito nele é igual ao deste resumo.
    """
    if E.resumo is None:
        raise HTTPException(503, "Resumo ainda não gerado: rode o pipeline (notebook 28).")
    return para_perfil(E.resumo, "gestor" if perfil == "gestor" else "cidadao")


@app.get("/api/eu")
def eu(u=Depends(usuario_atual)):
    return {"id": u["id"], "apelido": u["apelido"], "perfil": u["perfil"], "pontos": total_pontos(u["id"])}


@app.get("/api/catalogo")
def catalogo():
    return {"categorias": regras.CATEGORIAS, "volumes": regras.VOLUMES, "estados": regras.ROTULO_ESTADO, "respostas": regras.RESPOSTAS,
            "respostas_descarte": regras.RESPOSTAS_DESCARTE, "acoes_gestor": regras.ACOES_GESTOR, "transicoes": regras.EXPLICACAO_TRANSICOES,
            "regras_pontos": regras.REGRAS_PONTOS, "provisorio": "Estados, categorias, volumes e pontos são propostas de teste (11/11/2026), ainda não aprovadas.",
            "foto": "Envio de foto desativado nesta versão: a revisão de rostos e placas não está implementada."}


@app.get("/api/mapa/base")
def mapa_base():
    return {"bairros": E.dados.bairros_geojson, "faixas": E.dados.faixas_geojson, "vias": E.dados.vias_geojson,
            "fontes": "Bairros: Área urbana (Prefeitura do Recife). Faixas marginais: Prefeitura do Recife (base legal de 2003 e 2008)."}


@app.get("/api/mapa/risco")
def mapa_risco(u=Depends(usuario_atual)):
    """Cidadão recebe só o nível; gestor recebe score e motivos."""
    s, cfg = scores()
    s = s[s.via_m > 0]
    saida = []
    for r in s.itertuples():
        item = {"h3": r.h3, "nivel": r.faixa, "contorno": E.dados.contorno[r.h3]}
        if u["perfil"] == "gestor":
            item.update(score=r.score, regime=r.regime, motivos=list(r.motivos), bairro=r.bairro_nome, zona_silencio=bool(r.zona_silencio), na_fila=bool(r.em_fila))
        saida.append(item)
    return limpo({"hexagonos": saida, "cenario": cfg["cenario_ativo"],
                  "explicacao": "Nível de prioridade estimado por uma pontuação heurística provisória. Não é previsão comprovada nem contagem de chamados.",
                  "fontes": "Histórico do 156 (2024–2026), camadas públicas de vários anos e reportes do app (inclui exemplos simulados)."})


@app.get("/api/alertas")
def alertas(u=Depends(usuario_atual)):
    r = E.db.q("select * from reportes where estado<>'resolvido' order by atualizado_em desc")
    return {"alertas": [alerta_publico(x) for x in r], "privacidade": "Cada alerta aparece no centro do hexágono; quem reportou nunca é exibido."}


@app.post("/api/reportes")
def post_reporte(n: NovoReporte, u=Depends(cidadao)):
    with E.db.transacao():
        return criar_reporte(u, n.lat, n.lon, n.categoria, n.volume, n.texto, n.chave)


@app.post("/api/reportes/{rid}/confirmacoes")
def post_confirmacao(rid: str, c: Resposta, u=Depends(cidadao)):
    with E.db.transacao():
        return confirmar(u, rid, c.resposta, c.chave)


@app.get("/api/meus-reportes")
def meus_reportes(u=Depends(cidadao)):
    s, _ = scores()
    pr = s.set_index("h3")
    saida = []
    for r in E.db.q("select * from reportes where autor_id=? order by criado_em desc", (u["id"],)):
        l = pr.loc[r["h3"]]
        saida.append({"id": r["id"], "categoria": r["categoria"], "volume": regras.VOLUMES[r["volume"]], "bairro": r["bairro_nome"], "lat": r["lat"], "lon": r["lon"],
                      "estado": r["estado"], "estado_rotulo": regras.ROTULO_ESTADO[r["estado"]], "criado_em": r["criado_em"], "confirmacoes": confirmacoes_de(r["id"]),
                      "pode_confirmar_resultado": r["estado"] == "aguardando_confirmacao", "historico": historico(r["id"]),
                      "pode_reabrir": r['estado'] == 'resolvido' and datetime.fromisoformat(r['atualizado_em']) >= agora() - timedelta(days=regras.DIAS_PARA_REABRIR),
                      "prioridade_da_area": {"faixa": l.faixa, "na_fila": bool(l.em_fila)}})
    return limpo({"reportes": saida, "privacidade": "Só você vê esta lista e o ponto exato que marcou."})


@app.get("/api/contexto")
def contexto(lat: float, lon: float, u=Depends(usuario_atual)):
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(422, 'Coordenada inválida.')
    cel = h3.latlng_to_cell(lat, lon, H3_RES)
    if cel not in E.dados.h3s or not E.dados.dominio.covers(Point(lon, lat)):
        raise HTTPException(422, "O local fica fora da área coberta (Recife).")
    return contexto_do_hex(cel)


def situacao_descarte():
    c = pd.DataFrame(E.db.q("select ponto_id, resposta, usuario_id, em from descarte_confirmacoes"), columns=["ponto_id", "resposta", "usuario_id", "em"])
    return {pid: g.sort_values("em") for pid, g in c.groupby("ponto_id")}


@app.get("/api/descarte")
def descarte(material: str = "", u=Depends(usuario_atual)):
    d = E.dados.descarte
    if material:
        if material not in ("entulho", "volumosos", "poda", "reciclaveis", "eletronicos", "oleo", "pilhas", "pneus", "lampadas", "medicamentos", "organico"):
            raise HTTPException(422, "Material desconhecido.")
        d = d[d[material]]
    conf = situacao_descarte()
    saida = []
    for r in d.to_dict("records"):
        g = conf.get(r["ponto_id"])
        sit, verif, n = r["situacao"], r["data_verificacao"], 0
        if g is not None:
            ult = g.iloc[-1]
            n = int(g.usuario_id.nunique())
            sit = f"relato de participante do app: {regras.RESPOSTAS_DESCARTE[ult.resposta]} (não é verificação oficial)"
            verif = ult.em[:10]
        saida.append({"ponto_id": r["ponto_id"], "tipo": r["tipo"], "nome": r["nome"], "bairro": r["bairro_nome"] or r["bairro_fonte"], "endereco_na_fonte": r["endereco_fonte"],
                      "materiais": r["materiais"], "origem_material": r["origem_material"], "regra_uso": r["regra_uso"], "horario": r["horario"],
                      "situacao": sit, "data_verificacao": verif, "relatos_do_app": n, "localizacao": r["localizacao"], "lat": r["lat"], "lon": r["lon"],
                      "data_abertura": r["data_abertura"], "divergencia_registrada": r["divergencia_registrada"], "fontes": r["fontes"]})
    return limpo({"locais": saida, "materiais": [m for m in ["entulho", "volumosos", "poda", "reciclaveis", "eletronicos", "oleo", "pilhas", "pneus", "lampadas", "medicamentos", "organico"]],
                  "aviso": "Listas públicas históricas. Estar na lista não confirma funcionamento atual nem verifica a organização."})


@app.post("/api/descarte/{pid}/confirmacoes")
def post_descarte(pid: str, c: Resposta, u=Depends(cidadao)):
    with E.db.transacao():
        rep = evento_repetido(c.chave, u["id"], 'ponto_descarte_confirmado', pid, {'resposta': c.resposta, 'material': c.material})
        if rep:
            return rep
        if c.resposta not in regras.RESPOSTAS_DESCARTE or not (E.dados.descarte.ponto_id == pid).any():
            raise HTTPException(422, "Ponto ou resposta inválida.")
        eid = gravar_evento("ponto_descarte_confirmado", u, c.chave, pid, {"resposta": c.resposta, "material": c.material})
        E.db.x("insert into descarte_confirmacoes(ponto_id,usuario_id,resposta,material,evento_id,em) values(?,?,?,?,?,?)", (pid, u["id"], c.resposta, c.material, eid, iso()))
        pts = pontuar(u["id"], "descarte", f"{pid}:{iso()[:7]}", regras.PONTOS["descarte"], eid)
        missoes = []
        if E.db.um("select count(distinct ponto_id) n from descarte_confirmacoes where usuario_id=?", (u["id"],))["n"] >= 3:
            m = concluir_missao(u, "MS-DESCARTE-3", None)
            missoes = [m] if m else []
        return fechar_evento(eid, {"ponto_id": pid, "pontos_ganhos": pts, "missoes_concluidas": missoes})


@app.get("/api/missoes")
def missoes(u=Depends(cidadao)):
    feitas = {x["missao_id"] for x in E.db.q("select missao_id from missoes_concluidas where usuario_id=?", (u["id"],))}
    m = E.dados.missoes.drop(columns=["uso"], errors="ignore").to_dict("records")
    for x in m:
        x["concluida"] = x["missao_id"] in feitas
    return limpo({"missoes": m, "pontos": total_pontos(u["id"]), "regras": regras.REGRAS_PONTOS})


@app.get("/api/ranking")
def ranking(u=Depends(usuario_atual)):
    pessoas = E.db.q("""select u.apelido, coalesce(sum(p.pontos),0) pontos, u.origem from usuarios u left join pontos p on p.usuario_id=u.id
                        where u.perfil='cidadao' group by u.id order by pontos desc, u.apelido""")
    bairros = E.db.q("""select bairro_nome bairro, count(*) participacoes from (
                          select r.bairro_nome from reportes r where r.origem='uso_local'
                          union all select r.bairro_nome from confirmacoes c join reportes r on r.id=c.reporte_id join usuarios u on u.id=c.usuario_id where u.origem='uso_local')
                        where bairro_nome is not null group by bairro_nome order by participacoes desc limit 15""")
    for p in pessoas:
        p["exemplo_simulado"] = p.pop("origem") == "simulado"
    return {"participantes": pessoas, "bairros_por_participacao": bairros,
            "explicacao": "Ranking de PARTICIPAÇÃO (reportes e confirmações feitos no uso local). Não mede sujeira nem compara comunidades. "
                          "Participação também reflete onde há mais usuários."}


# ---- gestor
def reporte_gestor(r, pr):
    l = pr.loc[r["h3"]]
    return {"id": r["id"], "categoria": r["categoria"], "volume": regras.VOLUMES[r["volume"]], "bairro": r["bairro_nome"], "lat": r["lat"], "lon": r["lon"], "h3": r["h3"],
            "estado": r["estado"], "estado_rotulo": regras.ROTULO_ESTADO[r["estado"]], "criado_em": r["criado_em"], "atualizado_em": r["atualizado_em"],
            "confirmacoes": confirmacoes_de(r["id"]), "exemplo_simulado": r["origem"] == "simulado", "texto": r["texto"], "beira_de_rio": bool(r["beira_de_rio"]),
            "score_da_area": float(l.score), "faixa": l.faixa, "regime": l.regime, "motivos": list(l.motivos), "historico": historico(r["id"]),
            "autor": "pseudônimo oculto"}


@app.get("/api/gestor/fila")
def gestor_fila(u=Depends(gestor)):
    s, cfg = scores()
    pr = s.set_index("h3")
    abertos = E.db.q("select * from reportes where estado<>'resolvido' order by criado_em")
    por_hex = {}
    for r in abertos:
        por_hex.setdefault(r["h3"], []).append(reporte_gestor(r, pr))
    listas = {}
    for regime in ("morro", "planicie", "indefinido"):
        f = motor.fila(s, cfg, apenas_regime=regime)
        listas[regime] = [{"posicao": int(x.posicao), "h3": x.h3, "bairro": x.bairro_nome, "rpa": x.rpa, "score": x.score, "faixa": x.faixa, "motivos": list(x.motivos),
                           "zona_silencio": bool(x.zona_silencio), "lat": x.lat, "lon": x.lon, "reportes": por_hex.get(x.h3, []),
                           "componentes": {c: getattr(x, "contrib_" + c) for c in motor.COMPONENTES}} for x in f.itertuples()]
    na_fila = set(s[s.em_fila].h3)
    fora = [x for h, lst in por_hex.items() if h not in na_fila for x in lst]
    return limpo({"cenario": cfg["cenario_ativo"], "limiar": cfg["limiar_fila"], "capacidade_dia": cfg["capacidade_dia"], "filas": listas,
                  "reportes_fora_da_fila": fora, "total_acima_do_limiar": int(s.em_fila.sum()),
                  "aviso": "Pontuação heurística com pesos provisórios; cada lista é limitada à capacidade do dia. Exemplos simulados estão marcados."})


@app.get("/api/gestor/rota")
def gestor_rota(regime: str = "morro", setor: str | None = None, u=Depends(gestor)):
    if regime not in ('morro', 'planicie', 'indefinido'):
        raise HTTPException(422, 'Regime desconhecido.')
    s, cfg = scores()
    f = motor.fila(s, cfg, apenas_regime=regime)
    f = f.copy()
    f['coleta_setor'] = f.coleta_setor.fillna('sem setor')
    setores = list(f.coleta_setor.drop_duplicates())
    if setor is not None and setor not in setores:
        raise HTTPException(422, 'Setor sem itens nesta fila.')
    escolhido = setor or (setores[0] if setores else None)
    f = f[f.coleta_setor == escolhido]
    r = motor.sequencia_vizinho_mais_proximo(f)
    return limpo({"setor": escolhido, "setores": setores, "paradas": registros(r[["ordem", "h3", "bairro_nome", "score", "dist_reta_m", "lat", "lon"]]) if len(r) else [],
                  "total_linha_reta_km": round(float(r.dist_reta_m.sum()) / 1000, 1) if len(r) else 0,
                  "metodo": "vizinho mais próximo a partir do maior score, sobre distância em LINHA RETA entre centros de hexágono",
                  "aviso": "Sequência sugerida por heurística. Não é rota viária otimizada nem tempo de deslocamento."})


@app.get("/api/gestor/reportes")
def gestor_reportes(u=Depends(gestor)):
    s, _ = scores()
    pr = s.set_index("h3")
    return limpo({"reportes": [reporte_gestor(r, pr) for r in E.db.q("select * from reportes order by atualizado_em desc")]})


@app.post("/api/gestor/acoes")
def gestor_acao(a: Acao, u=Depends(gestor)):
    with E.db.transacao():
        rep = evento_repetido(a.chave, u["id"], 'acao_gestor', a.reporte_id, {'acao': a.acao, 'orgao': a.orgao, 'nota': a.nota})
        if rep:
            return rep
        r = E.db.um("select * from reportes where id=?", (a.reporte_id,))
        if not r:
            raise HTTPException(404, "Reporte não encontrado.")
        if a.acao not in regras.ACOES_GESTOR:
            raise HTTPException(422, "Ação desconhecida.")
        if a.acao != "vistoria":
            try:
                regras.proximo_estado(r["estado"], a.acao)
            except RegraViolada as e:
                raise HTTPException(e.codigo, e.mensagem)
        eid = gravar_evento("acao_gestor", u, a.chave, r["id"], {"acao": a.acao, "orgao": a.orgao, "nota": a.nota})
        E.db.x("insert into acoes_gestor(reporte_id,usuario_id,orgao,acao,nota,evento_id,em) values(?,?,?,?,?,?,?)", (r["id"], u["id"], a.orgao, a.acao, a.nota, eid, iso()))
        novo = transicionar(r, a.acao, eid, f"gestor: {regras.ACOES_GESTOR[a.acao]} ({a.orgao})") if a.acao != "vistoria" else r["estado"]
        return fechar_evento(eid, {"reporte_id": r["id"], "estado": novo, "estado_rotulo": regras.ROTULO_ESTADO[novo],
                                   "proximo_passo": "O cidadão será chamado a confirmar o resultado." if novo == "aguardando_confirmacao" else ""})


@app.get("/api/gestor/motor")
def get_motor(u=Depends(gestor)):
    return {"config": config_motor(), "componentes": motor.ROTULO, "padrao_do_arquivo": motor.carregar_config(),
            "aviso": "Valores de teste para revisão. Não são política operacional aprovada."}


@app.put("/api/gestor/motor")
def put_motor(cfg: dict, u=Depends(gestor)):
    try:
        cfg = motor.validar_config(cfg)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(422, f"Parâmetros inválidos: {e}")
    with E.db.transacao():
        E.db.x("insert into config(chave,valor) values('motor',?) on conflict(chave) do update set valor=excluded.valor", (json.dumps(cfg, ensure_ascii=False),))
        eid = "ev-" + uuid.uuid4().hex[:16]
        E.db.x("insert into eventos(id,tipo,usuario_id,origem,criado_em,chave_idempotencia,ref_id,payload,resultado) values(?,?,?,?,?,?,?,?,?)",
               (eid, "parametros_motor_alterados", u["id"], "uso_local", iso(), "cfg:" + eid, "motor", json.dumps(cfg, ensure_ascii=False), "{}"))
    s, _ = scores()
    return {"config": cfg, "acima_do_limiar": int(s.em_fila.sum())}


@app.delete("/api/gestor/motor")
def reset_motor(u=Depends(gestor)):
    with E.db.transacao():
        E.db.x("delete from config where chave='motor'")
        eid = gravar_evento('parametros_motor_restaurados', u, 'cfg:' + uuid.uuid4().hex, 'motor', motor.carregar_config())
        fechar_evento(eid, {'restaurado': True})
    return {"config": config_motor()}


@app.get("/api/gestor/silencio")
def gestor_silencio(u=Depends(gestor)):
    z = E.dados.silencio
    z = z[z.zona_silencio | z.silencio_inconclusivo]
    app_h3 = {x["h3"]: x["n"] for x in E.db.q("select h3, count(*) n from reportes where origem='uso_local' group by h3")}
    saida = registros(z[["h3", "bairro_nome", "rpa", "regime", "esperado_rank", "registrado_rank", "zona_silencio", "silencio_inconclusivo", "lat", "lon"]])
    for x in saida:
        x["reportes_do_app"] = app_h3.get(x["h3"], 0)
        x["contorno"] = E.dados.contorno.get(x["h3"])
    return {"zonas": saida, "metodo": z.metodo.iloc[0], "interpretacao": z.interpretacao.iloc[0], "fonte": z.fonte_dado.iloc[0]}


@app.get("/api/gestor/contexto")
def gestor_contexto(u=Depends(gestor)):
    r = E.dados.rotulos
    orc = r["mercado_orcamento_zeladoria"].groupby(["ano", "zeladoria"]).agg(valor=("valor", "sum"), pct_loa=("pct_loa", "sum"), por_hab=("valor_por_habitante_censo2022", "sum")).reset_index()
    d = r["base156_desempenho_mensal"].groupby("categoria").agg(chamados=("chamados", "sum"), elegiveis_30d=('elegiveis_30d', 'sum'), atendidos_30d=("atendidos_30d", "sum")).reset_index()
    d["pct_atendido_30d"] = (100 * d.atendidos_30d / d.elegiveis_30d).round(1)
    fila = r["base156_fila_aberta"].groupby(["categoria", "movimento", "faixa_idade"]).pedidos.sum().reset_index()
    return limpo({
        "orcamento": registros(orc), "orcamento_nota": "LOA 2023–2024: orçamento PREVISTO (dotação inicial), não gasto realizado. 1% de eficiência é ordem de grandeza, não economia comprovada.",
        "base156": registros(d), "fila156": registros(fila), "base156_nota": "Central 156 (Emlurb), 25 meses completos entre jan/2024 e mai/2026; chamados registrados, não toneladas. Fila: situação em 19/06/2026; separa as abertas que ainda se movem no arquivo de 2026 das paradas desde 2024–2025.",
        "teste_partida": registros(r["motor_teste_partida"].drop(columns=["uso"], errors="ignore")),
        "comparacao_esforco": registros(r['motor_comparacao_esforco'].drop(columns=['uso'], errors='ignore')),
        "auditoria_quartil": registros(r["motor_auditoria_quartil"].drop(columns=["uso"], errors="ignore")),
        "teste_nota": "Comparação descritiva com períodos separados (treino até ago/2025, teste jan–mai/2026). O score v0 não é previsão avaliada.",
        "agua": registros(r["painel_agua_residuo"].drop(columns=["uso"], errors="ignore")), "drenagem": registros(r["painel_drenagem"].drop(columns=["uso"], errors="ignore"))})


@app.get("/api/gestor/eventos")
def gestor_eventos(limite: int = 100, u=Depends(gestor)):
    ev = E.db.q("select id, tipo, origem, criado_em, ref_id from eventos order by criado_em desc, rowid desc limit ?", (min(limite, 500),))
    cont = E.db.q("select tipo, origem, count(*) n from eventos group by tipo, origem order by tipo")
    return {"eventos": ev, "contagem": cont, "nota": "Trilha de auditoria: eventos nunca são apagados nem editados. 'simulado' = exemplo; 'uso_local' = criado nesta máquina."}


@app.get("/api/b2b/demo")
def b2b(u=Depends(gestor)):
    d = E.dados.descarte
    parceiros = d[d.tipo.isin(["cooperativa", "parceiro ou outro", "ecoponto"])]
    por_mat = {m: int(parceiros[m].sum()) for m in ["reciclaveis", "eletronicos", "oleo", "pilhas", "pneus", "lampadas", "medicamentos"]}
    rep = E.db.q("select categoria, origem, count(*) n from reportes group by categoria, origem")
    return {"rotulo": "DEMONSTRAÇÃO — funcionalidade futura", "parceiros_por_material_listas_publicas": por_mat, "locais_listados": int(len(parceiros)),
            "reportes_por_categoria": rep,
            "avisos": ["Não há integração com MTR/Sinir. Não existe dado público de MTR no projeto.",
                       "Nada aqui comprova conformidade ESG ou logística reversa de qualquer empresa.",
                       "Os locais vêm de listas públicas históricas, não verificadas.",
                       "A frente B2B depende de entrevistas e de dados do próprio cliente."]}


@app.exception_handler(RegraViolada)
def _regra(_, e: RegraViolada):
    return JSONResponse(status_code=e.codigo, content={"detail": e.mensagem})


@app.get("/")
def inicio():
    """MVP atual: protótipo "Recife em Dados" (design da equipe, servido sem alterações visuais)."""
    return RedirectResponse("/recife/")


@app.get("/prototipo-anterior")
def prototipo_anterior():
    """Protótipo de 11/11 (reporte e confirmação), mantido só como histórico; fora do MVP atual."""
    return FileResponse(ESTATICO / "index.html")


app.mount("/recife", StaticFiles(directory=ESTATICO / "recife", html=True), name="recife")
app.mount("/static", StaticFiles(directory=ESTATICO), name="static")


def main():
    import uvicorn
    print("WasteStack: lendo as tabelas Gold e o banco local...", flush=True)
    iniciar()
    porta = int(os.environ.get("WASTESTACK_PORTA", "8156"))
    print(f"\nWasteStack — demonstração local em http://127.0.0.1:{porta}")
    print(f"Protótipo Recife em Dados: http://127.0.0.1:{porta}/  (versão anterior com abas: /recife/v1.html)")
    print("No login, use 'Usar acesso de demonstração' (perfil Gestor ou Cidadão).")
    print(f"Protótipo anterior de reporte (histórico): /prototipo-anterior · PIN do gestor: {E.pin_gestor}\n")
    # No Docker o servidor escuta em 0.0.0.0 dentro do contêiner; o docker-compose publica a porta só em 127.0.0.1.
    uvicorn.run(app, host=os.environ.get("WASTESTACK_HOST", "127.0.0.1"), port=porta, log_level="warning")


if __name__ == "__main__":
    main()
