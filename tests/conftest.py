import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """Aplicação com banco operacional novo e vazio em pasta temporária (não toca em data/operacional)."""
    monkeypatch.setenv("WASTESTACK_PIN_GESTOR", "4321")
    from app import servidor
    monkeypatch.setattr(servidor, "OPERACIONAL", tmp_path)
    servidor.E.db = None
    servidor.iniciar()
    from fastapi.testclient import TestClient
    cliente = TestClient(servidor.app)

    def entrar(uid, pin=""):
        r = cliente.post("/api/sessao", json={"usuario_id": uid, "pin": pin})
        assert r.status_code == 200, r.text
        return {"X-Sessao": r.json()["token"]}

    cliente.entrar = entrar
    cliente.servidor = servidor
    yield cliente
    servidor.E.db.con.close()
    servidor.E.db = None
