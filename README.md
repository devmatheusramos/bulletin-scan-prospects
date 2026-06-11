# Bulletin Scan — Prospects

Ferramenta de prospecção de igrejas católicas para o produto **Bulletin Scan** da Atimo.

## Estrutura

```
bulletin-scan-prospects/
├── scraper/
│   ├── refinar_prospects.py   ← edite as regras de scoring aqui
│   └── Dockerfile
├── app/
│   ├── mapa_prospects.html    ← mapa interativo (abre no browser)
│   ├── dashboard_prospects.html
│   └── prospects_data.js      ← gerado automaticamente (gitignored)
├── data/
│   └── prospects_refined.csv  ← CSV gerado (gitignored)
├── run.bat                    ← duplo clique para rodar tudo
└── .gitignore
```

## Como usar

### 1. Buscar dados frescos (recomendado)

Requer Docker instalado e rodando.

```bat
run.bat
```

Ou manualmente:
```bash
docker build -t bulletin-refiner scraper/
docker run --rm -v "%CD%:/project" bulletin-refiner
```

### 2. Ver resultados

Abra `app/mapa_prospects.html` no Chrome — os dados carregam automaticamente.

## Personalizar regras de scoring

Edite `scraper/refinar_prospects.py`:

- **`DIOCESES`** — adicione/remova regiões para buscar
- **`ATIMO_CLIENTES`** — igrejas excluídas (já são clientes)
- **`SCORING_RULES`** — pontos por condição (bulletin PDF, Facebook, email, etc.)
- **`classificar_lead()`** — thresholds de Hot / Warm / Cold
- **`SCRAPE_WORKERS`** — quantos sites scrapar em paralelo (padrão: 6)

Depois de editar, rode `run.bat` novamente.

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
| 🔥 Hot  | bulletin_found + score ≥ 3.5    |
| 🟡 Warm | bulletin_found OU score ≥ 2.5   |
| 🔵 Cold | tem site + score ≥ 1.0          |
| ⚫ None | sem dados suficientes             |
