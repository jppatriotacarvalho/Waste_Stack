"""Funções de limpeza compartilhadas entre os notebooks Silver."""
import re
import unicodedata

import pandas as pd


def sem_acento(texto):
    """Remove acentos e cedilha, mantendo o resto do texto."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return texto
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto)) if not unicodedata.combining(c))


def normalizar_nome(texto):
    """Chave de comparação: sem acento, maiúsculas, hífen vira espaço, espaço único."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return texto
    t = sem_acento(texto).upper().replace("-", " ")
    return re.sub(r"\s+", " ", t).strip()


# Apelidos de bairro (pasta 09, seção 1): forma normalizada -> forma canônica normalizada.
# A canônica é o nome curto da Área urbana (EBAIRRNOME). Conferir com a Área urbana no notebook 10.
APELIDOS_BAIRRO = {
    "COHAB IBURA DE CIMA": "COHAB",
    "SITIO DOS PINTOS SAO BRAS": "SITIO DOS PINTOS",
    "ALTO DO MANDU SITIO GRANDE": "ALTO DO MANDU",
    "BAIRRO DO RECIFE": "RECIFE",
    "RECIFE ANTIGO": "RECIFE",
    "POCO DA PANELA": "POCO",
    "ALTO STA TEREZINHA": "ALTO SANTA TEREZINHA",
    "JD SAO PAULO": "JARDIM SAO PAULO",
    "JD. SAO PAULO": "JARDIM SAO PAULO",
    "IBURA DE BAIXO": "IBURA",
    "BREJO BEBERIBE": "BREJO DE BEBERIBE",
    "BREJO GUABIRABA": "BREJO DA GUABIRABA",
    "ENGEMHO DO MEIO": "ENGENHO DO MEIO",
    "CAPUNGA": "BOA VISTA",
    "PAU FERRO": "PAU FERRO",  # Pau-Ferro normaliza para PAU FERRO
    "ZUMBI": "ZUMBI",
}


def chave_bairro(texto):
    """Nome de bairro normalizado e com apelidos resolvidos (para casar fontes diferentes)."""
    n = normalizar_nome(texto)
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return n
    return APELIDOS_BAIRRO.get(n, n)


def consertar_coordenada(valor, digitos_inteiros):
    """Conserta coordenadas estragadas pelo Google Sheets (ex.: '-80.852.089' -> -8.0852089).

    Tira tudo que não é dígito e põe o ponto decimal depois de `digitos_inteiros`
    dígitos: 1 para latitude do Recife (-8, -7) e 2 para longitude (-34, -35).
    Também funciona com coordenadas já corretas (ex.: '-8.0852089').
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    v = str(valor).strip()
    if not v:
        return None
    negativo = v.startswith("-")
    d = re.sub(r"\D", "", v)
    if not d:
        return None
    x = float(d[:digitos_inteiros] + "." + d[digitos_inteiros:]) if len(d) > digitos_inteiros else float(d)
    return -x if negativo else x


# Tipos de via por extenso -> abreviação usada pela Área urbana e pelo 156.
_TIPOS_VIA = {
    "RUA": "RUA", "R": "RUA", "AVENIDA": "AV", "AV": "AV", "TRAVESSA": "TRV", "TV": "TRV", "TRAV": "TRV", "TRV": "TRV",
    "SUBIDA": "SUB", "ESTRADA": "EST", "EST": "EST", "PRACA": "PRC", "CORREGO": "COR", "BECO": "BEC", "LADEIRA": "LAD",
    "VILA": "VIL", "LARGO": "LGO", "PONTE": "PTE", "ESCADARIA": "ESC", "RODOVIA": "ROD",
}


def padrao_logradouro(texto):
    """Leva um nome de rua por extenso ao padrão "TIPO NOME" da Área urbana (ex.: "1ª Travessa X" -> "1TR X").

    Só reescreve o tipo de via; não corrige grafias nem escolhe entre ruas parecidas.
    """
    n = normalizar_nome(texto)
    if n is None or (isinstance(n, float) and pd.isna(n)) or not n:
        return None
    n = re.sub(r"[.,;]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"^RUARUA\b", "RUA", n)
    m = re.match(r"^(\d{1,2})\s*[ªAº°O]?\s*(TRAVESSA|TRAV|TRV|TV)\b\s*(.*)$", n)
    if m:
        return f"{int(m.group(1))}TR {m.group(3)}".strip()
    partes = n.split(" ", 1)
    if partes[0] in _TIPOS_VIA and len(partes) > 1:
        return f"{_TIPOS_VIA[partes[0]]} {partes[1]}"
    return n
