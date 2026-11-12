"""Preparação autorizada de cópia da drenagem sem autoria de usuários."""

import hashlib
import json
from pathlib import Path


def preparar_drenagem_sem_usuarios(origem_restrita, destino):
    """Remove somente os dois atributos de autoria; não altera o original.

    Isso não constitui certificação de anonimização. Os valores removidos
    nunca entram no relatório retornado.
    """
    origem_restrita, destino = Path(origem_restrita), Path(destino)
    bruto = origem_restrita.read_bytes()
    objeto = json.loads(bruto.decode("utf-8-sig"))
    if objeto.get("type") != "FeatureCollection":
        raise ValueError("Esperado GeoJSON FeatureCollection")
    campos = ("CREATED_USER", "LAST_EDITED_USER")
    removidos = {campo: 0 for campo in campos}
    preenchidos = {campo: 0 for campo in campos}
    for feicao in objeto["features"]:
        propriedades = feicao.get("properties") or {}
        for campo in campos:
            if campo in propriedades:
                removidos[campo] += 1
                preenchidos[campo] += bool(str(propriedades[campo] or "").strip())
                del propriedades[campo]
    preparado = (json.dumps(objeto, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    # Conferir que nenhuma outra propriedade, geometria ou metadado mudou.
    original = json.loads(bruto.decode("utf-8-sig"))
    reaberto = json.loads(preparado.decode("utf-8"))
    for feicao in original["features"]:
        for campo in campos:
            (feicao.get("properties") or {}).pop(campo, None)
    if reaberto != original:
        raise ValueError("A transformação alterou conteúdo além dos campos autorizados")
    if any(campo in (f.get("properties") or {}) for f in reaberto["features"] for campo in campos):
        raise ValueError("Ainda há campos de autoria na cópia preparada")
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        if destino.read_bytes() != preparado:
            raise ValueError("Destino já existe com outro conteúdo; preservado")
    else:
        with destino.open("xb") as arquivo:
            arquivo.write(preparado)
    return {
        "transformacao": "remoção autorizada de CREATED_USER e LAST_EDITED_USER",
        "feicoes_preservadas": len(objeto["features"]),
        "campos_removidos": removidos,
        "valores_preenchidos_removidos": preenchidos,
        "sha256_original": hashlib.sha256(bruto).hexdigest(),
        "sha256_preparado": hashlib.sha256(preparado).hexdigest(),
        "demais_propriedades_geometrias_metadados_preservados": True,
    }
