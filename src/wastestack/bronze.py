"""Conferência de CSV, GeoJSON e ZIP com Shapefile ou CSV no Bronze."""

import csv
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path


def _inspecionar_csv(dados):
    try:
        texto = dados.decode("utf-8-sig")
        codificacao = "utf-8-sig" if dados.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        # Leitura reversível do CSV IBGE recebido; não altera sua codificação.
        texto = dados.decode("cp1252")
        codificacao = "cp1252"
    separador = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;\t").delimiter
    leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=separador)
    campos = next(leitor)
    quantidade = 0
    for numero, linha in enumerate(leitor, start=2):
        if not linha:
            continue
        if len(linha) != len(campos):
            raise ValueError(f"CSV: quantidade de campos inválida na linha {numero}")
        quantidade += 1
    return dict(codificacao=codificacao, separador=separador, campos=campos,
                linhas_de_dados=quantidade, colunas=len(campos))


def inspecionar_arquivo(caminho):
    """Retorna integridade, formato e contagens sem alterar o arquivo."""
    caminho = Path(caminho)
    dados = caminho.read_bytes()
    resultado = {
        "md5": hashlib.md5(dados).hexdigest(),
        "sha256": hashlib.sha256(dados).hexdigest(),
        "bytes": len(dados),
        "codificacao": "utf-8-sig" if dados.startswith(b"\xef\xbb\xbf") else "utf-8",
    }
    if caminho.suffix.lower() == ".zip":
        # Ler dentro do ZIP preserva o original e evita extrair caminhos arbitrários.
        with zipfile.ZipFile(io.BytesIO(dados)) as arquivo:
            if arquivo.testzip() is not None:
                raise ValueError(f"{caminho.name}: falha de integridade do ZIP")
            membros = arquivo.namelist()
            shapes = [nome for nome in membros if nome.lower().endswith(".shp")]
            csvs = [nome for nome in membros if nome.lower().endswith(".csv")]
            if not shapes and len(csvs) == 1:
                resultado.update(_inspecionar_csv(arquivo.read(csvs[0])))
                resultado.update(membro_csv=csvs[0], membros_zip=membros)
                return resultado
            if len(shapes) != 1:
                raise ValueError(f"{caminho.name}: esperado um único Shapefile ou CSV no ZIP")
            base = shapes[0][:-4]
            dbf = arquivo.read(base + ".dbf")
            indice = arquivo.read(base + ".shx")
            geometria = arquivo.read(shapes[0])
            quantidade, cabecalho, tamanho_registro = struct.unpack_from("<IHH", dbf, 4)
            if cabecalho < 33 or (cabecalho - 33) % 32 or not tamanho_registro:
                raise ValueError(f"{caminho.name}: cabeçalho DBF inválido")
            if len(dbf) < cabecalho + quantidade * tamanho_registro:
                raise ValueError(f"{caminho.name}: DBF incompleto")
            if len(indice) != 100 + quantidade * 8:
                raise ValueError(f"{caminho.name}: contagem SHX e DBF divergente")
            for componente in (indice, geometria):
                if len(componente) < 100 or struct.unpack_from(">I", componente)[0] != 9994:
                    raise ValueError(f"{caminho.name}: cabeçalho Shapefile inválido")
                if struct.unpack_from(">I", componente, 24)[0] * 2 != len(componente):
                    raise ValueError(f"{caminho.name}: tamanho Shapefile incorreto")
            # Sem registros DBF apagados: a contagem representa as feições do arquivo.
            if any(dbf[cabecalho + i * tamanho_registro] != 32 for i in range(quantidade)):
                raise ValueError(f"{caminho.name}: DBF contém registro apagado ou inválido")
            campos = [
                dbf[i:i + 11].split(b"\0")[0].decode("ascii")
                for i in range(32, cabecalho - 1, 32)
            ]
            resultado.update(
                codificacao=arquivo.read(base + ".cpg").decode("ascii").strip(),
                separador=None,
                campos=campos,
                linhas_de_dados=quantidade,
                colunas=len(campos),
                membro_shapefile=shapes[0],
                membros_zip=membros,
                crs_wkt=arquivo.read(base + ".prj").decode("utf-8-sig"),
            )
        return resultado
    if caminho.suffix.lower() == ".csv":
        resultado.update(_inspecionar_csv(dados))
    elif caminho.suffix.lower() in {".geojson", ".json"}:
        objeto = json.loads(dados.decode("utf-8-sig"))
        if objeto.get("type") != "FeatureCollection" or not isinstance(objeto.get("features"), list):
            raise ValueError(f"{caminho.name}: esperado GeoJSON FeatureCollection")
        feicoes = objeto["features"]
        campos = sorted({campo for feicao in feicoes for campo in (feicao.get("properties") or {})})
        resultado.update(
            separador=None,
            campos=campos,
            linhas_de_dados=len(feicoes),
            crs_declarado=objeto.get("crs"),
            tipos_geometria=sorted({f["geometry"]["type"] for f in feicoes if f.get("geometry")}),
        )
    else:
        raise ValueError(f"Formato não suportado: {caminho.suffix}")
    resultado["colunas"] = len(resultado["campos"])
    return resultado
