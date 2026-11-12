"""Regras PROVISÓRIAS da demonstração local (hipóteses de teste autorizadas em 11/11/2026, para revisão).

Estados do reporte e transições, pontos simbólicos e limites contra duplicação. Nada aqui é regra
operacional aprovada. Este módulo não acessa banco nem rede: só decide.
"""

CATEGORIAS = ["entulho", "lixo doméstico", "volumoso", "poda", "recicláveis", "ponto de descarte cheio",
              "canaleta ou galeria entupida", "lixo na beira de rio ou canal"]
VOLUMES = {"saco": "cabe em sacos", "carrinho": "um carrinho de mão", "cacamba": "uma caçamba ou mais"}
ESTADOS = ["recebido", "confirmado", "em_atendimento", "aguardando_confirmacao", "resolvido", "reaberto"]
ROTULO_ESTADO = {"recebido": "Recebido", "confirmado": "Confirmado pela comunidade", "em_atendimento": "Em atendimento",
                 "aguardando_confirmacao": "Aguardando confirmação do resultado", "resolvido": "Resolvido", "reaberto": "Reaberto"}
RESPOSTAS = {"ainda": "ainda está aqui", "limpo": "já foi limpo", "nao_encontrei": "não encontrei"}
ACOES_GESTOR = {"vistoria": "Vistoria registrada", "iniciar_atendimento": "Atendimento iniciado", "concluir_limpeza": "Limpeza concluída"}
RESPOSTAS_DESCARTE = {"funciona": "funciona", "fechado": "fechado", "mudou": "mudou de lugar", "nao_recebe": "não recebe o material"}

# (estado atual, gatilho) -> novo estado. O que não está aqui é recusado com explicação.
TRANSICOES = {
    ("recebido", "comunidade_confirma"): "confirmado",
    ("recebido", "iniciar_atendimento"): "em_atendimento",
    ("confirmado", "iniciar_atendimento"): "em_atendimento",
    ("reaberto", "iniciar_atendimento"): "em_atendimento",
    ("em_atendimento", "concluir_limpeza"): "aguardando_confirmacao",
    ("aguardando_confirmacao", "resultado_confirmado"): "resolvido",
    ("aguardando_confirmacao", "resultado_negado"): "reaberto",
    ("resolvido", "comunidade_reabre"): "reaberto",
    ("recebido", "baixa_comunidade"): "resolvido",
    ("confirmado", "baixa_comunidade"): "resolvido",
}
EXPLICACAO_TRANSICOES = [
    "recebido → confirmado: outra pessoa (não o autor) responde 'ainda está aqui'.",
    "recebido, confirmado ou reaberto → em atendimento: o gestor registra 'atendimento iniciado'.",
    "em atendimento → aguardando confirmação: o gestor registra 'limpeza concluída'. O gestor não encerra sozinho.",
    "aguardando confirmação → resolvido: um cidadão (pode ser o autor) responde 'já foi limpo'.",
    "aguardando confirmação → reaberto: um cidadão responde 'ainda está aqui'.",
    "resolvido → reaberto: um cidadão responde 'ainda está aqui' em até 30 dias.",
    "recebido ou confirmado → resolvido (baixa da comunidade): duas pessoas diferentes do autor respondem 'já foi limpo' ou 'não encontrei'.",
    "Vistoria do gestor fica no histórico e não muda o estado.",
]

PONTOS = {"reporte": 10, "confirmacao": 5, "reporte_confirmado": 5, "descarte": 5}
BONUS_AREA = 2  # zona de silêncio ou beira de rio: pontos do reporte em dobro
LIMITE_REPORTES_HORA = 5
DEDUP_METROS, DEDUP_HORAS = 30, 72
BAIXAS_PARA_ENCERRAR = 2
DIAS_PARA_REABRIR = 30
REGRAS_PONTOS = [
    "Reportar: +10 (em dobro em zona de silêncio ou beira de rio).",
    "Confirmar alerta de outra pessoa: +5, uma vez por alerta e por ciclo.",
    "Seu reporte confirmado por outra pessoa: +5, uma vez.",
    "Confirmar ponto de descarte: +5, uma vez por ponto a cada mês.",
    "Missão concluída: pontos da missão, uma vez.",
    "Se duas pessoas não encontrarem o que você reportou, os pontos do reporte são retirados.",
    f"Limites: até {LIMITE_REPORTES_HORA} reportes por hora; reporte da mesma categoria a até {DEDUP_METROS} m e {DEDUP_HORAS} h de um alerta ativo vira confirmação.",
    "O mesmo envio repetido (mesma chave) nunca pontua duas vezes.",
    "Pontos são simbólicos. O ranking mede participação, nunca 'sujeira' de bairro ou comunidade.",
]


class RegraViolada(Exception):
    def __init__(self, mensagem, codigo=409):
        super().__init__(mensagem)
        self.mensagem, self.codigo = mensagem, codigo


def proximo_estado(estado, gatilho):
    novo = TRANSICOES.get((estado, gatilho))
    if novo is None:
        raise RegraViolada(f"Ação incoerente: '{gatilho}' não se aplica a um reporte '{ROTULO_ESTADO.get(estado, estado)}'.")
    return novo


def gatilho_da_confirmacao(estado, resposta, eh_autor, baixas_distintas):
    """Traduz a resposta de um cidadão no gatilho de transição (ou None se só fica registrada)."""
    if resposta not in RESPOSTAS:
        raise RegraViolada("Resposta inválida.", 422)
    if estado in ("recebido", "confirmado"):
        if eh_autor:
            raise RegraViolada("Quem reportou não confirma o próprio alerta enquanto ele está aberto.", 403)
        if resposta == "ainda":
            return "comunidade_confirma" if estado == "recebido" else None
        return "baixa_comunidade" if baixas_distintas + 1 >= BAIXAS_PARA_ENCERRAR else None
    if estado == "aguardando_confirmacao":
        return {"limpo": "resultado_confirmado", "ainda": "resultado_negado"}.get(resposta)
    if estado == "resolvido":
        if resposta == "ainda":
            return "comunidade_reabre"
        raise RegraViolada("Este reporte já está resolvido.")
    if estado == "em_atendimento":
        raise RegraViolada("O atendimento está em curso. A confirmação abre quando o gestor registrar a conclusão.")
    if estado == "reaberto":
        if eh_autor:
            raise RegraViolada("Quem reportou não confirma o próprio alerta enquanto ele está aberto.", 403)
        return None
    raise RegraViolada("Estado desconhecido.")
