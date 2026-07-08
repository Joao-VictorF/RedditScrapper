# reddit-scrapper

Scraper para endpoints publicos JSON do Reddit, com saida JSONL para pipeline de chunk, embedding e RAG.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Entrada de links especificos

Crie um arquivo links.txt na raiz, com um link por linha.

Exemplo:

```text
https://www.reddit.com/r/MachineLearning/comments/abc123/example_post/
https://www.reddit.com/r/MachineLearning/comments/def456/another_post/
```

## Coleta por subreddit em janela de datas (recomendado)

```bash
python3 src/main.py \
	--subreddit MachineLearning \
	--start-date 2026-01-01 \
	--end-date 2026-06-30 \
	--run-label semester-1 \
	--max-posts 0 \
	--links-file links.txt \
	--results-root results/runs \
	--requests-per-minute 10
```

Notas:

- O fetch do subreddit e feito por `new.json` com filtro por `created_utc`.
- `--max-posts 0` significa sem limite (usa apenas a janela de datas).
- Isso facilita rodar por semestres e ir incrementando a base.
- `--run-label` ajuda a diferenciar execucoes (ex.: `semester-1`, `week-27`, `retry-high-depth`).

## Estrutura organizada por run

Cada execucao cria um diretório proprio em `results/runs`:

```text
results/runs/
	subreddit=machinelearning/
		period=2026-01-01_to_2026-06-30/
			run=20260708T143221Z/
				corpus.jsonl
				pending_comments.jsonl
				checkpoint.json
				summary.json
				inputs/
					links.txt
```

			Com label:

			```text
			results/runs/subreddit=machinelearning/period=2026-01-01_to_2026-06-30/run=20260708T143221Z__label=semester-1/
			```

`run_id` e o timestamp UTC da execucao no formato `YYYYMMDDTHHMMSSZ`.

Isso permite organizar runs por dia/semana/mes/semestre/ano sem sobrescrever artefatos.

## Apenas links.txt

```bash
python3 src/main.py --links-file links.txt --output corpus.jsonl
```

## Retomar execucao sem reler posts

Por padrao o script salva checkpoint e retoma automaticamente, evitando reler os mesmos links ja processados.

```bash
python3 src/main.py \
	--subreddit MachineLearning \
	--start-date 2026-01-01 \
	--end-date 2026-06-30 \
	--output corpus.jsonl \
	--checkpoint-file checkpoint.json
```

Se quiser ignorar checkpoint e rodar do zero:

```bash
python3 src/main.py --subreddit MachineLearning --no-resume
```

## Campos salvos no JSONL

Cada linha inclui metadados do post e comentarios achatados:

- id, subreddit, title, selftext, author, score, num_comments, created_utc
- permalink, url, domain, over_18, spoiler, locked, stickied
- comments: lista com body, score, depth, author, created_utc

## Observacoes importantes

- URL de post + .json normalmente retorna o post e parte da arvore de comentarios.
- Nem sempre vem tudo: podem existir blocos do tipo more, threads continuadas e limitacoes de profundidade.
- Use User-Agent proprio e limite conservador de requests.

## Metricas no final da execucao

Ao terminar, o script imprime:

- Saved: posts gravados
- Failed: posts com falha
- ExpectedComments: soma de num_comments dos posts
- ExtractedComments: comentarios realmente extraidos (kind t1)
- PendingCommentIds: ids de comentarios pendentes (vindos de blocos more)
- Coverage: ExtractedComments / ExpectedComments
- MorePlaceholders: quantidade de blocos more encontrados

Exemplo:

```text
Summary Saved=200 Failed=0 ExpectedComments=1000 ExtractedComments=800 PendingCommentIds=340
Averages ExtractedPerPost=4.00 PendingIdsPerPost=1.70
```

## Novos arquivos gerados

- `corpus.jsonl`: dados brutos do run
- `pending_comments.jsonl`: fila de pendencias por post com ids de comentarios faltantes
- `checkpoint.json`: estado de progresso para retomar aquele run
- `summary.json`: resumo completo daquela execucao
- `inputs/links.txt`: snapshot do arquivo de links usado no run (se existir)

## JSON vs JSONL

- JSON unico: um unico arquivo com um array gigante. Pior para append e para recuperar em caso de queda.
- JSONL: uma linha JSON por documento. Melhor para processamento incremental, reprocessamento parcial e pipelines de RAG.

Para crawler de longa duracao, JSONL e mais robusto.

