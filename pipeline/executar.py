"""Executa os notebooks do pipeline em ordem, sem precisar do Jupyter, e grava as saídas dentro deles.

Uso (na raiz do repositório, com a .venv ativa):
    python pipeline/executar.py                # cadeia inteira: Bronze -> Silver -> Gold -> validação
    python pipeline/executar.py 10 11          # só os notebooks cujo nome começa por 10 e 11
    python pipeline/executar.py --a-partir 20  # do 20 em diante

Cada notebook chama funções de src/wastestack/ (a lógica não é duplicada aqui). Um erro interrompe a cadeia.
"""
import ast
import contextlib
import io
import json
import os
import sys
import time
import traceback
import warnings
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
NOTEBOOKS = RAIZ / "notebooks"
sys.path.insert(0, str(RAIZ / "src"))


def _mostrar(valor):
    try:
        import pandas as pd
        if isinstance(valor, (pd.DataFrame, pd.Series)):
            with pd.option_context("display.max_rows", 40, "display.max_columns", 30, "display.width", 200):
                return valor.drop(columns="geometry", errors="ignore").to_string() if isinstance(valor, pd.DataFrame) else valor.to_string()
    except Exception:
        pass
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False, indent=1, default=str)
    return repr(valor)


def executar_notebook(caminho, gravar=True):
    caminho = Path(caminho)
    nb = json.loads(caminho.read_text(encoding="utf-8"))
    ns = {"__name__": "__main__"}
    anterior = os.getcwd()
    os.chdir(caminho.parent)
    contador, ok = 0, True
    try:
        for celula in nb["cells"]:
            if celula["cell_type"] != "code":
                continue
            fonte = "".join(celula["source"])
            contador += 1
            saida = io.StringIO()
            try:
                arvore = ast.parse(fonte)
                ultimo = arvore.body.pop() if arvore.body and isinstance(arvore.body[-1], ast.Expr) else None
                with contextlib.redirect_stdout(saida), warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    exec(compile(arvore, f"{caminho.name}[{contador}]", "exec"), ns)
                    if ultimo is not None:
                        valor = eval(compile(ast.Expression(ultimo.value), f"{caminho.name}[{contador}]", "eval"), ns)
                        if valor is not None:
                            print(_mostrar(valor))
                celula["outputs"] = [{"output_type": "stream", "name": "stdout", "text": saida.getvalue().splitlines(True)}] if saida.getvalue() else []
            except Exception:
                ok = False
                texto = saida.getvalue() + traceback.format_exc()
                celula["outputs"] = [{"output_type": "stream", "name": "stderr", "text": texto.splitlines(True)}]
                print(texto, file=sys.stderr)
            celula["execution_count"] = contador
            if not ok:
                break
    finally:
        os.chdir(anterior)
    if gravar:
        caminho.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return ok


def main(argv):
    relatorio = {"inicio": datetime.now(timezone.utc).isoformat(), "argumentos": argv.copy(), "etapas": []}
    def registrar():
        relatorio['fim'] = datetime.now(timezone.utc).isoformat()
        pasta = RAIZ / 'data' / 'validacao'
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / 'ultima_execucao_pipeline.json').write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding='utf-8')
    nomes = sorted(p for p in NOTEBOOKS.glob("[0-9][0-9]_*.ipynb"))
    if "--a-partir" in argv:
        inicio = argv[argv.index("--a-partir") + 1]
        nomes = [p for p in nomes if p.name[:2] >= inicio]
        argv = []
    filtros = [a for a in argv if not a.startswith("--")]
    if filtros:
        nomes = [p for p in nomes if any(p.name.startswith(f) for f in filtros)]
    for p in nomes:
        t = time.time()
        print(f"== {p.name}", flush=True)
        ok = executar_notebook(p)
        relatorio['etapas'].append({'notebook': p.name, 'aprovado': ok, 'segundos': round(time.time() - t, 2)})
        registrar()
        if not ok:
            print(f"FALHOU: {p.name}")
            return 1
        print(f"   ok em {time.time() - t:.0f}s", flush=True)
    print("Cadeia concluída.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
