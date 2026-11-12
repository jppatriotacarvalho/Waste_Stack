"""Caminhos e constantes do pipeline WasteStack."""
from pathlib import Path

# Raiz do repositório (src/wastestack/config.py -> sobe 2 níveis)
RAIZ = Path(__file__).resolve().parents[2]
DATA = RAIZ / "data"
BRONZE = DATA / "bronze"
SILVER = DATA / "silver"
GOLD = DATA / "gold"

# Projeção para medidas em metros (SIRGAS 2000 / UTM 25S) e de armazenamento
EPSG_METROS = 31985
EPSG_WGS84 = 4326

# Resolução da grade H3 aprovada pela equipe em 10/11/2026.
H3_RES = 9

# Caixa de validação de coordenadas do Recife (receita da pasta 08)
LAT_MIN, LAT_MAX = -8.16, -7.92
LON_MIN, LON_MAX = -35.02, -34.85
