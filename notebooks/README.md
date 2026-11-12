# Notebooks do pipeline (Bronze → Silver → Gold)

Cada notebook só lê a camada anterior, usa as funções de `src/wastestack/` e grava Parquet/GeoParquet com um `.meta.json` (linhagem, hash e contagem). Especificação: pasta 12 dos documentos do projeto.

Para rodar tudo sem Jupyter: `python pipeline/executar.py` (ou `python pipeline/executar.py 21 22` para etapas específicas, `--a-partir 20` para refazer as Gold). As saídas das células ficam gravadas nos próprios notebooks.

| Notebook | Camada | O que faz | Status (12/11/2026) |
| --- | --- | --- | --- |
| 01_bronze_ingestao | Bronze | Confere as entradas do catálogo (hash, bytes, registros, colunas) | executado e aprovado |
| 10_silver_territorio | Silver | Contorno IBGE × bairros, bairros, logradouros (ruas-rio e nomes ambíguos marcados), água, faces de quadra (sem geometria), grade H3 res. 9 | executado e aprovado |
| 11_silver_populacao | Silver | Censo 2022 por bairro; `v0001` = Total de pessoas conferido no dicionário oficial; 94/94 bairros, 1.488.920 pessoas, nada sem ligação | executado e aprovado |
| 12_silver_156 | Silver | Receita da pasta 08 sem NUMERO; 230.491 demandas e 61.790 no recorte (diferença esperada da referência registrada) | executado e aprovado |
| 13_silver_faixas | Silver | Faixas marginais (classe, união, simplificada) | executado e aprovado |
| 14_silver_descarte | Silver | Pontos de coleta + Destinação + Ecoestações 2023 + lista de apoio das 17 ecoestações; origem de cada coordenada; 7 unidades sem coordenada mantidas sem posição | executado e aprovado |
| 15_silver_defesa_civil | Silver | Ações 2023–2024 (2025 tem uma linha e não entra); saída restrita | executado e aprovado |
| 16_silver_nucleos | Silver | Setores de coleta, drenagem (sem autoria) e esgoto 2022 | executado e aprovado |
| 17_silver_cis | Silver | CIS 2014, marca de CIS pequena | executado e aprovado |
| 18_silver_polos | Silver | Feiras e mercados juntados em polos | executado e aprovado |
| 19_silver_orcamento | Silver | LOA 2023–2024 (dotação, não gasto realizado) | executado e aprovado |
| 20_gold_fonte_unica | Gold | G-U1 a G-U8 (orçamento, polos, setores, faixas, CIS do gestor, encosta, mapa base, perfil de serviço, linha de base do 156) | executado e aprovado |
| 21_gold_historico_156 | Gold | Localização do 156 (coordenada ou rua+bairro repartida; sem ponto fictício) e histórico por hexágono × mês | executado e aprovado |
| 22_gold_features_h3 | Gold | Feature store; regime morro/planície/indefinido com confiança | executado e aprovado |
| 23_gold_zonas_silencio | Gold | Esperado × registrado; hipótese de baixa cobertura | executado e aprovado |
| 24_gold_onde_descartar | Gold | Tabela integrada do app, filtrável por material | executado e aprovado |
| 25_gold_motor_score | Gold | Score v0, teste de partida em períodos separados, auditoria por quartil, fila e sequência | executado e aprovado |
| 26_gold_paineis | Gold | Painéis C3, C4, C9, C10, C11 (C7 não calculado: coordenadas das unidades novas pendentes) | executado e aprovado |
| 27_gold_app | Gold | Contexto local, missões e exemplos simulados | executado e aprovado |
| 28_gold_dados_publicos | Gold | Resumo por tema da página "Recife em Dados" (`dados_publicos_resumo.json`); só agregados | executado e aprovado (12/11) |
| 90_validacao_integrada | — | 105 verificações de dados + 17 testes (API, regras e protótipo) | executado e aprovado |

Teste da interface (fora do pytest, precisa do servidor e de um navegador): `tests/e2e_interface.py`, que hoje confere o protótipo "Recife em Dados". `tests/test_prototipo.py` exige que cada número do design seja igual ao do notebook 28. Ver o README da raiz.

## Execução registrada

- **12/11/2026:** cadeia completa 01 → 90 reexecutada do zero, etapa por etapa, numa cópia Linux (Python 3.10, pandas 2.3.3, geopandas 1.1.4, h3 4.5.0, duckdb 1.5.6) sobre os mesmos arquivos; todas aprovadas. Validação: 104 verificações; testes: 13 da API e o teste de ponta a ponta da interface (24 conferências) aprovados. Os notebooks 01 a 25 já tinham sido executados em 11/11 no Windows (`.venv`, Python 3.14).
- Correções de 12/11: mapa base de vias passou a guardar só linhas (12 recortes tinham pontos soltos que a tela desenhava como marcadores); verificação nova de que `AREA_KM2` do IBGE (218,843 km²) é reproduzido na projeção de Albers do IBGE, o que explica a diferença para a medida em UTM (218,914 km²); a fila aberta do 156 passou a separar as demandas que ainda se movem no arquivo de 2026 (como na análise do 156) das paradas desde 2024–2025. Na mesma definição: drenagem aberta 2.612 (ref. 2.662), drenagem parada 7.343 (ref. 7.494), recorte aberto 5.912 (ref. 6.018), entre −1,8% e −2,0%, coerente com a deduplicação sem NUMERO.

## Decisões e limites registrados

- **156 sem NUMERO (10/11/2026):** deduplicação por serviço + logradouro + bairro + data de abertura. Sem NUMERO não é possível comprovar endereços distintos; ruas do 156 ficam só para o gestor.
- **Bordas do município:** o domínio da demonstração é a união IBGE 2025 + bairros; as divergências (0,446 km² do IBGE sem bairro; 1,584 km² de bairros fora do IBGE) ficam preservadas, sem recorte de bairros nem descarte silencioso de pontos.
- **Localização do 156:** 94,2% do recorte localizado (38,3% por coordenada, 55,9% por rua+bairro, repartido pelos hexágonos da rua). Precisão por rua mediana de ~428 m; não é o ponto do chamado.
- **Teste de partida:** nos 10% de hexágonos de maior score, o cenário equilibrado captura 22,4% dos chamados de jan–mai/2026; o histórico simples, 28,8%; as ruas com mais histórico, 34,3% (mesmo km de via). O score **não** é previsor de chamados e não supera a referência; o resultado está registrado sem ajuste.
- **Zonas de silêncio:** 64 de 1.033 hexágonos urbanizados; hipótese a investigar, não prova de presença ou ausência de lixo.
- **Alagamento:** não há camada de alagamento da cidade; a Defesa Civil serve a encosta e a ações, não a um mapa de alagamentos.
- **Nota metodológica MMD 2025:** continua inacessível (403 e fora da lista de acesso); foi consultada a edição 2024 (Notas metodológicas 01/2025), que descreve o cálculo de `AREA_KM2` em Albers e o caráter aproximado dos limites.

- **MVP atual (12/11/2026):** a equipe definiu o primeiro MVP como visualização dos dados públicos ("Recife em Dados", design recebido do Claude Design). O notebook 28 gera os números da página; o protótipo de reporte de 11/11 fica como histórico em `/prototipo-anterior`.
