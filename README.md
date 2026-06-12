# Bulletin Scan — Prospects

Ferramenta de prospecção de igrejas católicas para o produto **Bulletin Scan** da Atimo.

## Estrutura

```
bulletin-scan-prospects/
├── scraper/
│   ├── refinar_prospects.py   ← edite as regras de scoring aqui
│   └── Dockerfile             ← imagem isolada do scraper (legado)
├── web/
│   ├── server.py              ← servidor FastAPI
│   └── templates/
│       ├── dashboard_prospects.html
│       └── mapa_prospects.html
├── data/
│   └── prospects_refined.csv  ← gerado pelo scraper (gitignored)
├── Dockerfile                 ← imagem do servidor web
├── docker-compose.yml
└── run.bat                    ← duplo clique para subir tudo
```

## Como usar

### 1. Subir o servidor

```bat
run.bat
```

Ou manualmente:
```bash
docker compose up --build
```

- Acessa em: http://localhost:8000
- Dashboard:  http://localhost:8000/dashboard
- Mapa:       http://localhost:8000/mapa

### 2. Buscar dados frescos (sem parar o servidor)

```bash
curl -X POST http://localhost:8000/api/scrape
```

### 3. Ver status do scraper + stats

```bash
curl http://localhost:8000/api/status
```

### 4. Rodar scraper direto (sem servidor)

```bash
docker compose --profile scraper run --rm scraper
```

## API

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/dashboard` | GET | Dashboard com KPIs e emails |
| `/mapa` | GET | Mapa interativo com filtros |
| `/api/stats` | GET | Contagens hot/warm/cold/total |
| `/api/data` | GET | JSON com todos os prospects |
| `/api/status` | GET | Status do scraper + stats |
| `/api/scrape` | POST | Inicia novo scraping em background |

## Personalizar regras de scoring

Edite `scraper/refinar_prospects.py`:

- **`DIOCESES`** — adicione/remova regiões para buscar
- **`ATIMO_CLIENTES`** — igrejas excluídas (já são clientes)
- **`SCORING_RULES`** — pontos por condição (bulletin PDF, Facebook, email, etc.)
- **`classificar_lead()`** — thresholds de Hot / Warm / Cold
- **`SCRAPE_WORKERS`** — quantos sites scrapar em paralelo (padrão: 6)

Depois de editar, dispare um novo scrape:
```bash
curl -X POST http://localhost:8000/api/scrape
```

## Lead scoring padrão

| Condição           | Pontos |
|--------------------|--------|
| Tem website        | +1.0   |
| Tem email direto   | +1.0   |
| Tem telefone       | +0.5   |
| Bulletin PDF       | +3.0   |
| Facebook           | +0.5   |
| Instagram          | +0.5   |
| YouTube            | +0.5   |
| Diocese FL         | +0.5   |
| Sem social/email   | -0.5   |

| Lead  | Critério                          |
|-------|-----------------------------------|
| Hot  | bulletin_found + score ≥ 3.5    |
| Warm | bulletin_found OU score ≥ 2.5   |
| Cold | tem site + score ≥ 1.0          |
| None | sem dados suficientes             |
