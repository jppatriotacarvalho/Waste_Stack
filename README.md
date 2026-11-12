# WasteStack — dados públicos de resíduos do Recife

Projeto da startup WasteStack (Grupo 9).

**MVP atual (12/11/2026): "Recife em Dados"**, uma página que mostra de forma simples o que os dados públicos dizem sobre a limpeza da cidade. Ela tem login com perfil **Gestor** ou **Cidadão**, menu lateral e a página **Dados públicos**. O visual é o design aprovado pela equipe (Claude Design, sistema "Organic"), servido sem alterações. Os números vêm do pipeline Bronze → Silver → Gold, e um teste confere cada número escrito no design.

Os dados abertos do Recife (Central 156, Área urbana, Faixas Marginais, CIS, Defesa Civil, coleta, descarte, feiras, LOA) e o Censo 2022 são a base. Registros e chamados não são toneladas de lixo; cada achado traz fonte, período e ressalvas.

> **Versão local de demonstração.** Nada está publicado. O login do protótipo é de demonstração: valida o formato do e-mail e da senha na própria página, não é controle de acesso.

## Como abrir

### Opção 1 — Python (Windows, PowerShell, na raiz do repositório)

```powershell
.venv\Scripts\activate
pip install -r requirements.txt            # só na primeira vez
python -m app.servidor                     # abre em http://127.0.0.1:8156
```

Na primeira vez no Windows/OneDrive, o carregamento das bibliotecas pode levar alguns minutos: espere a mensagem com o endereço (não aperte Ctrl+C).

### Opção 2 — Docker (Docker Desktop instalado)

```powershell
docker compose up --build                  # abre em http://127.0.0.1:8156
docker compose --profile pipeline run --rm pipeline   # refaz Bronze -> Silver -> Gold -> validação
docker compose --profile testes run --rm testes       # testes da API e do protótipo
```

O Docker usa as pastas `data/` e `config/` do seu computador (volume): o que o pipeline gera fica aqui. A porta é publicada só em `127.0.0.1`, então nada fica exposto na rede. As dependências da imagem estão em `requirements-app.txt` (sem Jupyter, Playwright e matplotlib, que são de desenvolvimento).

## Sequência curta para revisar

1. Abra http://127.0.0.1:8156 e clique em **Usar acesso de demonstração** com o perfil **Gestor**. Entre.
2. Veja a página **Dados públicos**: os quatro números do topo, atendimento e recorrência do 156, lacunas de serviço, fila de drenagem, Defesa Civil, onde descartar, prioridade, carência × reclamação e **Leia com cuidado**.
3. O gestor vê **Exportar relatório** e a referência técnica de cada cartão ("Fonte: notebooks …").
4. Clique em **Sair**, escolha **Cidadão** e entre de novo: a mesma página, sem exportação e sem as referências técnicas.
5. A versão anterior do design, com abas por tema, continua em http://127.0.0.1:8156/recife/v1.html.

## Pipeline de dados

```powershell
python pipeline\executar.py                # cadeia inteira: notebooks 01 a 28 e 90 (alguns minutos)
python pipeline\executar.py 28 90          # só o resumo da página e a validação
python -m pytest tests                     # 17 testes (API, regras, protótipo)
```

O notebook `28_gold_dados_publicos` gera `data/gold/dados_publicos_resumo.json`, com os números de cada tema. Ele lê a Silver, as Gold e as validações já gravadas pelo pipeline. O teste `tests/test_prototipo.py` exige que cada número escrito no design seja igual ao desse resumo. Se os dados mudarem, o teste falha e avisa que o protótipo precisa ser atualizado.

Teste de ponta a ponta pela tela (opcional; precisa do servidor rodando e do Chromium do Playwright):

```powershell
python -m playwright install chromium      # só na primeira vez
python tests\e2e_interface.py http://127.0.0.1:8156
```

## Estrutura

```
Waste_Stack/
├── app/
│   ├── servidor.py              API local (FastAPI); a raiz abre o protótipo
│   └── static/recife/           protótipo "Recife em Dados" (design da equipe): index.html (v2) e v1.html (abas)
│       ├── _ds/…/styles.css     sistema visual Organic, com as fontes Caprasimo e Figtree servidas localmente
│       └── vendor/              React 18.3.1 e Babel 7.29.0 locais (as mesmas versões que o design pede)
├── data/
│   ├── bronze/                  dados brutos por fonte e lote; _restrito/ fora do Git
│   ├── silver/  gold/           geradas pelo pipeline (fora do Git)
│   ├── operacional/             banco local do protótipo anterior (fora do Git)
│   └── validacao/               evidências das verificações e capturas de tela (fora do Git)
├── src/wastestack/              lógica compartilhada (bronze, território, silver, gold, motor, dados_publicos, validação)
├── notebooks/                   uma etapa por notebook (ver notebooks/README.md)
├── pipeline/executar.py         roda os notebooks em ordem, sem Jupyter
├── tests/                       pytest e teste da interface
├── Dockerfile  docker-compose.yml  .dockerignore  requirements-app.txt
└── requirements.txt             ambiente completo de desenvolvimento
```

O protótipo de 11/11 (reporte, confirmação, motor e painel do gestor) continua no código e acessível em `/prototipo-anterior`, como histórico. Ele não faz parte do MVP atual.

## Escolhas técnicas

- **Pipeline:** Python + GeoPandas + H3 + DuckDB, Parquet/GeoParquet; medidas em SIRGAS 2000 / UTM 25S (EPSG:31985).
- **Interface:** o arquivo exportado do Claude Design, servido como veio. Só três ajustes técnicos, para funcionar sem internet: React e Babel locais (via `window.__resources`, recurso do próprio runtime do design), fontes locais no lugar do Google Fonts e retirada dos links do Google Fonts. Nada visual muda.
- **Sem serviços externos:** a página não acessa CDN, Google Fonts nem mapas de terceiros (conferido no teste da interface).
- **Docker:** imagem `python:3.12-slim`, usuário sem privilégios, dados por volume, porta só local.

## Regras de privacidade

- **Bronze não muda:** originais com dado pessoal ficam em `data/bronze/_restrito/` (fora do Git). No 156, `NUMERO` foi esvaziada antes do Bronze; na drenagem, a autoria foi retirada (o que não certifica anonimização).
- **Página:** só números agregados (cidade, bairro, RPA, categoria). Nenhuma rua do 156, nenhuma comunidade nomeada, nenhuma ação da Defesa Civil por rua e dia.
- **Regras públicas:** rua com 10+ endereços e 10+ registros; hexágono com 5+ registros de 3+ endereços; mês fechado; "menos de 5"; CIS sem ranking nominal. Sem `NUMERO`, o 156 não permite comprovar endereços distintos.

## Fontes

Portal de Dados Abertos do Recife: https://dados.recife.pe.gov.br · IBGE: Censo 2022 (Agregados por Bairro) e Malha Municipal Digital 2025. Proveniência, hashes e datas em `data/bronze/_catalogo_bronze.csv` e `data/bronze/_recebimentos/`. Fontes Caprasimo e Figtree: SIL Open Font License 1.1 (licenças em `app/static/recife/_ds/…/fontes/`).
