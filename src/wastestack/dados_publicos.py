"""Página "Dados públicos" (Recife em Dados): resumo por tema gerado pelo pipeline.

`gerar_resumo()` roda no notebook 28: lê a Silver, as Gold e as validações já gravadas pelo pipeline e
escreve `data/gold/dados_publicos_resumo.json`. A aplicação só lê esse arquivo: nenhum número da página é
digitado à mão. Saída só agregada (cidade, bairro, RPA, categoria): sem ruas, sem nomes de comunidades,
sem ações da Defesa Civil por rua e dia.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .config import GOLD, SILVER

TREINO_FIM, TESTE = "2025-08", ("2026-01", "2026-05")   # mesmos cortes do notebook 25
MATERIAIS = [("reciclaveis", "Recicláveis"), ("eletronicos", "Eletrônicos"), ("oleo", "Óleo de cozinha"), ("entulho", "Entulho"),
             ("volumosos", "Volumosos"), ("poda", "Poda"), ("organico", "Orgânico"), ("pilhas", "Pilhas"), ("pneus", "Pneus"),
             ("lampadas", "Lâmpadas"), ("medicamentos", "Medicamentos")]
DIAS = ["seg", "ter", "qua", "qui", "sex", "sab", "dom"]


def _json(pasta, nome):
    return json.loads((pasta / f"validacao_{nome}.json").read_text(encoding="utf-8"))


def _r(v, casas=1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), casas)


def gerar_resumo(silver=SILVER, gold=GOLD):
    silver, gold = Path(silver), Path(gold)
    g = lambda n: pd.read_parquet(gold / f"{n}.parquet")
    v = {n: _json(silver, n) for n in ("contorno", "populacao", "territorio", "faixas", "defesa_civil", "nucleos", "cis",
                                       "descarte", "polos", "localizacao_156")}
    vg = {n: _json(gold, n) for n in ("fonte_unica", "paineis")}

    # ---- território
    cobertura = pd.read_parquet(silver / "cobertura_bairro_contorno.parquet").sort_values("area_fora_ibge_km2", ascending=False)
    perfil = g("perfil_servico_bairro")
    pond = lambda c: _r((perfil[c] * perfil.faces).sum() / perfil.faces.sum())
    comp = g("motor_componentes_h3")

    # ---- Central 156 (mesmas regras dos notebooks 12 e 25)
    o = pd.read_parquet(silver / "ocorrencia_156.parquet")
    rec = o[o.no_recorte & o.periodo_analise]
    cats = {"lixo": "Lixo e descarte", "vias": "Limpeza de vias", "drenagem": "Limpeza de drenagem"}
    med = rec.groupby("categoria").dias_ate_atender.median()
    t = o[o.no_recorte & o.mes_completo].copy()
    t["mes"] = t.data_demanda.dt.strftime("%Y-%m")
    t["rua"] = t.bairro_chave.fillna("") + "|" + t.logradouro_norm.fillna("")
    tr, te = t[t.mes <= TREINO_FIM], t[(t.mes >= TESTE[0]) & (t.mes <= TESTE[1])]
    recorrencia = 100 * te.rua.isin(set(tr.rua)).mean()

    # ---- morros, coleta, descarte, feiras, cobertura
    feat = g("motor_features_h3")
    com_via = feat.via_m >= 200
    sem_dren = (100 * feat[com_via].assign(sem=lambda x: x.drenagem_m == 0).groupby("rpa")["sem"].mean()).dropna()
    rpa_max = sem_dren.idxmax()
    desc = g("app_onde_descartar")
    eco = desc[desc.tipo == "ecoestação"]
    teste = g("motor_teste_partida")
    pct = lambda inicio: _r(teste[teste.criterio.str.startswith(inicio)].pct_chamados_2026_capturados.iloc[0])
    sil = g("silencio_bairro")
    orc = g("mercado_orcamento_zeladoria")
    ano = int(orc.ano.max())
    limp = orc[(orc.ano == ano) & orc.zeladoria.str.startswith("limpeza")]
    dias = v["polos"]["feiras_abertas_por_dia"]

    r = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aviso": "Registros e chamados não são toneladas de lixo. Cada tema traz fonte, período e ressalvas.",
        "temas": {
            "territorio": {
                "fonte": "IBGE (Censo 2022, Malha Municipal 2025) e Prefeitura do Recife (Área urbana)", "periodo": "Censo 2022; plano de limpeza sem data na fonte",
                "fonte_tecnica": "notebooks 10 e 11",
                "bairros": int(v["populacao"]["bairros_casados"]), "populacao": int(v["populacao"]["populacao_total"]),
                "hexagonos": int(len(comp)), "populacao_sem_ligacao": int(v["populacao"]["populacao_sem_ligacao"]),
                "pct_sobreposicao": _r(v["contorno"]["pct_ibge_coberto_bairros"]),
                "beira_mar": list(cobertura.bairro_nome.head(3)),
                "plano": {"Sem galeria registrada": pond("pct_sem_galeria"), "Limpeza mensal ou nenhuma": pond("pct_limpeza_mensal_ou_sem"),
                          "Coleta manual": pond("pct_coleta_manual")},
                "rios_rua": int(v["territorio"]["rios_removidos_malha"]),
            },
            "central156": {
                "fonte": "Central 156 (Emlurb), versão sem a coluna NUMERO", "periodo": "jan/2024 a jun/2026",
                "fonte_tecnica": "notebooks 12 e 21",
                "total": int(len(o)), "recorte": int(len(rec)),
                "por_categoria": {k: int((rec.categoria == c).sum()) for k, c in cats.items()},
                "mediana_dias": {k: _r(med.get(c), 0) for k, c in cats.items()},
                "pct_coordenada": _r(100 * o.coord_valida.mean()), "pct_mapeados": _r(v["localizacao_156"]["recorte_pct_localizado"]),
                "pct_recorrencia": _r(recorrencia), "ruas_20_meses": int(vg["fonte_unica"]["ruas_20_de_25_meses"]),
                "fila_drenagem": {"abertos": int(vg["fonte_unica"]["drenagem_aberta_com_movimento_2026"]),
                                  "parados": int(vg["fonte_unica"]["drenagem_parada_2024_2025"])},
            },
            "agua": {
                "fonte": "Prefeitura do Recife (Faixas marginais) e Central 156", "periodo": "faixas: base legal de 2003 e 2008; 156: jan/2024 a jun/2026, só chamados com coordenada",
                "fonte_tecnica": "notebooks 13 e 26",
                "pct_area_faixa": _r(v["faixas"]["pct_area_bairros_em_faixa"]),
                "razoes": [{"categoria": k, "valor": _r(x, 2)} for k, x in sorted(vg["paineis"]["agua_observado_sobre_esperado"].items(), key=lambda kv: kv[1])],
            },
            "morros": {
                "fonte": "Defesa Civil do Recife (registro de atendimentos)", "periodo": "2023–2024",
                "fonte_tecnica": "notebooks 15 e 22",
                "acoes": int(v["defesa_civil"]["registros_2023_2024"]), "pct_morro": _r(v["defesa_civil"]["pct_regional_morro"]),
                "ruas_r4": int(v["defesa_civil"]["ruas_r4"]), "lona_m2": _r(v["defesa_civil"]["area_lona_m2"], 0),
                "regime": {k: int((comp.regime == k).sum()) for k in ("morro", "planicie", "indefinido")},
                "alagamento_registros": int(v["defesa_civil"]["alagamento_registros"]), "alagamento_dias": int(v["defesa_civil"]["dias_com_alagamento"]),
            },
            "coleta": {
                "fonte": "Prefeitura do Recife (setores de coleta, drenagem, esgoto), Atlas das CIS e Central 156", "periodo": "setores e redes sem vigência confirmada; CIS: 2014",
                "fonte_tecnica": "notebooks 16, 17, 22 e 26",
                "setores_regulares": int(v["nucleos"]["setores_regulares"]), "setores_seletiva": int(v["nucleos"]["setores_seletiva"]),
                "pct_sem_seletiva": _r(100 * (~comp[comp.via_m > 0].tem_seletiva.astype(bool)).mean()),
                "pct_dia_sem_coleta": _r(vg["paineis"]["plano_alternados_pct_em_dia_sem_coleta"]),
                "drenagem_km": _r(v["nucleos"]["drenagem_km"], 0), "esgoto_km": _r(v["nucleos"]["esgoto_km"], 0),
                "rpa_sem_drenagem": {"rpa": int(rpa_max), "pct": _r(sem_dren[rpa_max], 0)},
                "cis": int(v["cis"]["cis"]), "cis_populacao_2014": int(v["cis"]["populacao_estimada_2014"]), "cis_deficit": int(v["cis"]["com_deficit_coleta"]),
            },
            "descarte": {
                "fonte": "Prefeitura do Recife: Pontos de coleta, Destinação de resíduos e Ecoestações", "periodo": "listas sem data de verificação",
                "fonte_tecnica": "notebooks 14 e 24",
                "locais": int(len(desc)),
                "materiais": [{"rotulo": rot, "valor": int(desc[c].fillna(False).astype(bool).sum())} for c, rot in MATERIAIS],
                "ecoestacoes": int(len(eco)), "ecoestacoes_sem_coordenada": int(eco.lat.isna().sum()),
                "desertos": int(vg["paineis"]["desertos_de_descarte"]), "hexagonos": int(len(comp)),
            },
            "feiras": {
                "fonte": "Conviva (feiras e mercados, cadastro parcial) e LOA do Recife", "periodo": f"orçamento previsto de {ano}",
                "fonte_tecnica": "notebooks 18 e 19",
                "feiras": int(v["polos"]["feiras"]), "mercados": int(v["polos"]["mercados"]),
                "feiras_por_dia": [{"dia": d, "feiras": int(dias[d])} for d in DIAS],
                "limpeza_ano": ano, "limpeza_valor": _r(limp.valor.sum(), 0),
            },
            "cobertura": {
                "fonte": "Cruzamento WasteStack: Central 156, faces de quadra, CIS, coleta e Censo 2022", "periodo": "ajuste até ago/2025; conferência em jan–mai/2026",
                "fonte_tecnica": "notebooks 23 e 25",
                "correlacao": _r(sil[["chamados_por_mil_hab", "carencia_media"]].corr(method="spearman").iloc[0, 1], 2),
                "zonas": int(g("motor_esperado_registrado_h3").zona_silencio.sum()),
                "teste": {"ruas": pct("ruas com mais histórico"), "score": pct("score v0 — cenário equilibrado"),
                          "historico": pct("histórico simples"), "acaso": pct("acaso")},
            },
        },
    }
    (gold / "dados_publicos_resumo.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


def para_perfil(resumo, perfil):
    """O cidadão recebe tudo menos a referência técnica (notebooks); o gestor recebe tudo."""
    if perfil == "gestor":
        return resumo
    copia = json.loads(json.dumps(resumo))
    for t in copia["temas"].values():
        t.pop("fonte_tecnica", None)
    return copia
