"""Motor de priorização v0: pontuação heurística, explicável e editável (NÃO é previsão avaliada).

    score = 100 × min(1, Σ peso_i × componente_i × fator_zona_de_silêncio)

Componentes (0 a 1) por hexágono H3:
  reincidencia     histórico do 156 localizado, com mais peso para os meses recentes
  app              reportes ativos do app (volume e confirmações)
  vulnerabilidade  carência estrutural (CIS 2014, V0, sem seletiva, sem drenagem, coleta manual, longe de descarte)
  exposicao        morro: encosta R3/R4, escadaria, coleta manual · planície: faixa marginal de rio
  contexto         polo de comércio perto, setor de coleta alternada

Os pesos mudam por regime territorial (morro, planície, indefinido) e por cenário. O mesmo código
serve à Gold (notebook 25) e à aplicação local, para que a fila da tela seja a da tabela.
Só depende de pandas/numpy.
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

COMPONENTES = ["reincidencia", "app", "vulnerabilidade", "exposicao", "contexto"]
ROTULO = {"reincidencia": "histórico do 156", "app": "reportes do app", "vulnerabilidade": "carência estrutural",
          "exposicao": "exposição (encosta ou beira de rio)", "contexto": "contexto (comércio, coleta alternada)"}
VOLUME_PESO = {"saco": 0.25, "carrinho": 0.5, "cacamba": 1.0}
ESTADOS_ATIVOS = ("recebido", "confirmado", "reaberto", "em_atendimento")
CONFIG_PADRAO = Path(__file__).resolve().parents[2] / "config" / "motor_cenarios.json"


def carregar_config(caminho=None):
    return validar_config(json.loads(Path(caminho or CONFIG_PADRAO).read_text(encoding="utf-8")))


def validar_config(cfg):
    """Confere a forma dos parâmetros editáveis; levanta ValueError com mensagem clara."""
    if cfg.get("cenario_ativo") not in cfg.get("cenarios", {}):
        raise ValueError("cenario_ativo precisa existir em cenarios")
    if not 0 <= float(cfg["limiar_fila"]) <= 100:
        raise ValueError("limiar_fila deve ficar entre 0 e 100")
    if not float(cfg['capacidade_dia']).is_integer() or not 1 <= int(cfg["capacidade_dia"]) <= 500:
        raise ValueError("capacidade_dia deve ficar entre 1 e 500")
    if not 1 <= float(cfg["fator_zona_silencio"]) <= 2:
        raise ValueError("fator_zona_silencio deve ficar entre 1 e 2")
    for nome, cen in cfg["cenarios"].items():
        for regime in ("morro", "planicie", "indefinido"):
            pesos = cen.get(regime)
            if not pesos or set(pesos) != set(COMPONENTES):
                raise ValueError(f"{nome}/{regime}: informe os cinco pesos {COMPONENTES}")
            if any(not math.isfinite(float(v)) or float(v) < 0 for v in pesos.values()) or sum(map(float, pesos.values())) <= 0:
                raise ValueError(f"{nome}/{regime}: pesos não podem ser negativos nem somar zero")
    return cfg


def componente_app(reportes):
    """Reportes ativos -> componente 0–1 por hexágono. `reportes`: colunas h3, estado, volume, confirmacoes."""
    if reportes is None or len(reportes) == 0:
        return pd.Series(dtype=float)
    r = reportes[reportes.estado.isin(ESTADOS_ATIVOS)]
    if r.empty:
        return pd.Series(dtype=float)
    forca = r.volume.map(VOLUME_PESO).fillna(0.25) * (1 + 0.5 * r.confirmacoes.clip(upper=3))
    return (forca.groupby(r.h3).sum() / 2).clip(upper=1)


def calcular_score(componentes, cfg, app=None, cenario=None):
    """Aplica os pesos do cenário a cada hexágono e devolve score, faixa e os motivos em texto.

    `componentes`: uma linha por h3 com regime, c_reincidencia, c_vulnerabilidade, c_exposicao, c_contexto,
    zona_silencio e os textos motivo_*. `app`: Series h3 -> componente do app (ausente = 0).
    """
    cen = cfg["cenarios"][cenario or cfg["cenario_ativo"]]
    d = componentes.copy()
    d["c_app"] = d.h3.map(app).fillna(0.0) if app is not None and len(app) else 0.0
    regime = d.regime.where(d.regime.isin(["morro", "planicie"]), "indefinido")
    contrib = {}
    soma_pesos = regime.map(lambda r: sum(float(v) for v in cen[r].values()))
    for c in COMPONENTES:
        peso = regime.map(lambda r: float(cen[r][c])) / soma_pesos
        contrib[c] = peso * d["c_" + c].fillna(0.0)
    base = sum(contrib.values())
    fator = np.where(d.zona_silencio.fillna(False), float(cfg["fator_zona_silencio"]), 1.0)
    d["score"] = (100 * (base * fator).clip(upper=1)).round(1)
    d["faixa"] = pd.cut(d.score, [-1, 25, 50, 75, 101], labels=["baixo", "médio", "alto", "crítico"]).astype(str)
    cm = pd.DataFrame(contrib)
    for c in COMPONENTES:
        d["contrib_" + c] = (100 * cm[c]).round(1)
    ordem = np.argsort(-cm.values, axis=1)[:, :3]
    textos = d[["motivo_" + c for c in COMPONENTES if c != "app"]].to_dict("list")
    motivos = []
    for i, linha in enumerate(ordem):
        itens = []
        for k in linha:
            c = COMPONENTES[k]
            if cm.values[i, k] <= 0.005:
                continue
            txt = "reportes ativos do app nesta área" if c == "app" else textos["motivo_" + c][i]
            itens.append(f"{txt} (+{100 * cm.values[i, k]:.0f})")
        if fator[i] > 1:
            itens.append(f"zona de silêncio: pouco registro para a carência (×{fator[i]:.2f})")
        motivos.append(itens)
    d["motivos"] = motivos
    d["em_fila"] = d.score >= float(cfg["limiar_fila"])
    return d


def fila(scores, cfg, apenas_regime=None):
    """Itens acima do limiar, do maior score para o menor, limitados à capacidade do dia."""
    f = scores[scores.em_fila]
    if apenas_regime:
        f = f[f.regime == apenas_regime]
    f = f.sort_values(["score", "h3"], ascending=[False, True]).head(int(cfg["capacidade_dia"])).copy()
    f["posicao"] = range(1, len(f) + 1)
    return f


def sequencia_vizinho_mais_proximo(itens, x="x_m", y="y_m"):
    """Ordem de visita por vizinho mais próximo, começando no item de maior score.

    Heurística documentada sobre distância EM LINHA RETA entre centros de hexágono. Não é rota viária
    otimizada nem estimativa de tempo de deslocamento.
    """
    if len(itens) == 0:
        return itens.assign(ordem=[], dist_reta_m=[])
    resto = itens.sort_values("score", ascending=False).to_dict("records")
    atual = resto.pop(0)
    saida = [dict(atual, ordem=1, dist_reta_m=0.0)]
    while resto:
        d = [float(np.hypot(r[x] - atual[x], r[y] - atual[y])) for r in resto]
        i = int(np.argmin(d))
        atual = resto.pop(i)
        saida.append(dict(atual, ordem=len(saida) + 1, dist_reta_m=round(d[i], 0)))
    return pd.DataFrame(saida)
