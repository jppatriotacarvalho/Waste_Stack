"""Teste de ponta a ponta pela INTERFACE (Chrome sem tela) do protótipo "Recife em Dados".

Confere: o protótipo abre sem acessar a internet (React, fontes e estilos locais), sem erros de JavaScript;
o login valida e-mail e senha; "Usar acesso de demonstração" entra como Gestor e como Cidadão; o gestor vê
"Exportar relatório" e as fontes técnicas, o cidadão não; a página mostra os achados e o quadro
"Leia com cuidado"; a versão anterior com abas (/recife/v1.html) navega entre os 9 botões.

Uso (com o servidor já rodando em outro terminal):
    python tests/e2e_interface.py http://127.0.0.1:8156

Requer `pip install playwright` e `python -m playwright install chromium` (ou WASTESTACK_CHROME com o caminho de um
Chrome/Edge instalado). Capturas e resultado em data/validacao/e2e/ (fora do Git).
"""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8156").rstrip("/")
OUT = Path(__file__).resolve().parents[1] / "data" / "validacao" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)
log, externos, erros = [], [], []


def ok(cond, msg):
    log.append(("OK " if cond else "FALHA ") + msg)
    print(log[-1], flush=True)
    if not cond:
        raise AssertionError(msg)


def so_local(rota):
    if rota.request.url.startswith(URL):
        return rota.continue_()
    externos.append(rota.request.url)
    return rota.abort()


def entrar(page, perfil):
    page.goto(URL + "/")
    page.wait_for_selector("text=Escolha seu perfil de acesso.")
    page.locator("label.seg-opt", has_text=perfil).click()
    page.get_by_text("Usar acesso de demonstração").click()
    page.locator("form button[type=submit]").click()
    page.wait_for_selector("text=O que os dados dizem sobre a limpeza da cidade")
    page.wait_for_timeout(800)


with sync_playwright() as p:
    chrome = os.environ.get("WASTESTACK_CHROME")
    nav = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"]) if chrome else p.chromium.launch()
    page = nav.new_page(viewport={"width": 1440, "height": 900})
    page.set_default_timeout(60000)
    page.route("**/*", so_local)
    page.on("pageerror", lambda e: erros.append(str(e)))

    # 1. login
    page.goto(URL + "/")
    page.wait_for_selector("text=Escolha seu perfil de acesso.")
    ok(page.url.endswith("/recife/"), "a raiz abre o protótipo Recife em Dados")
    ok(page.evaluate("document.fonts.check('40px Caprasimo') && document.fonts.check('16px Figtree')"), "fontes do design carregadas localmente")
    page.fill("#email", "nome@semdominio")
    page.fill("#senha", "1234")
    page.locator("form button[type=submit]").click()
    ok(page.get_by_text("Informe um e-mail válido.").is_visible(), "login recusa e-mail inválido")
    page.screenshot(path=str(OUT / "01_login.png"))

    # 2. gestor
    entrar(page, "Gestor")
    corpo = page.inner_text("body")
    for trecho in ["61.790", "93,4%", "9.955", "R$ 309,3", "Drenagem tem menos chamados", "Onde a cidade não é atendida",
                   "Reciclável tem 106 locais", "O histórico ainda acerta mais", "Quem mais precisa não reclama mais", "Leia com cuidado"]:
        ok(trecho in corpo, f"gestor vê: {trecho}")
    ok(page.get_by_role("button", name="Exportar relatório").is_visible(), "gestor tem o botão Exportar relatório")
    ok("Fonte: notebooks" in corpo, "gestor vê as fontes técnicas")
    page.screenshot(path=str(OUT / "02_gestor.png"), full_page=True)
    page.get_by_role("button", name="Sair").click()

    # 3. cidadão
    entrar(page, "Cidadão")
    corpo = page.inner_text("body")
    ok("61.790" in corpo and "Leia com cuidado" in corpo, "cidadão vê os mesmos achados")
    ok(page.get_by_role("button", name="Exportar relatório").count() == 0, "cidadão não tem exportação")
    ok("Fonte: notebooks" not in corpo, "cidadão não vê as referências técnicas")
    page.screenshot(path=str(OUT / "03_cidadao.png"), full_page=True)

    # 4. versão anterior com abas
    page.goto(URL + "/recife/v1.html")
    page.wait_for_selector("text=Escolha seu perfil de acesso.")
    page.get_by_text("Usar acesso de demonstração").click()
    page.locator("form button[type=submit]").click()
    page.wait_for_selector("text=Visão geral")
    for aba, titulo in [("Território", "Território e população"), ("Central 156", "Chamados de jan/2024"), ("Água e rios", "margem de rio"),
                        ("Morros", "Morros e Defesa Civil"), ("Coleta e redes", "Coleta, redes e comunidades"), ("Descarte", "Onde descartar"),
                        ("Feiras e orçamento", "Feiras e orçamento"), ("Cobertura", "Cobertura e prioridade")]:
        page.get_by_role("button", name=aba, exact=True).first.click()
        page.wait_for_timeout(250)
        ok(titulo in page.inner_text("main"), f"v1: aba {aba}")

    ok(not externos, f"nenhum acesso à internet ({externos[:2]})")
    ok(not erros, f"sem erros de JavaScript ({erros[:2]})")
    nav.close()

Path(OUT / "resultado.json").write_text(json.dumps({"aprovado": True, "passos": log}, ensure_ascii=False, indent=1), encoding="utf-8")
print("PROTÓTIPO APROVADO")
