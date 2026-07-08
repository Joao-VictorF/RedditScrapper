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

## Coleta por subreddit e links.txt ao mesmo tempo

```bash
python3 src/main.py \
	--subreddit MachineLearning \
	--listing top \
	--time-filter year \
	--max-posts 300 \
	--links-file links.txt \
	--output corpus.jsonl \
	--requests-per-minute 10
```

## Apenas links.txt

```bash
python3 src/main.py --links-file links.txt --output corpus.jsonl
```

## Retomar execucao sem reler posts

Por padrao o script salva checkpoint e retoma automaticamente, evitando reler os mesmos links ja processados.

```bash
python3 src/main.py \
	--subreddit MachineLearning \
	--max-posts 1000 \
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
- Coverage: ExtractedComments / ExpectedComments
- MorePlaceholders: quantidade de blocos more encontrados

Exemplo:

```text
Summary Saved=200 Failed=0 ExpectedComments=1000 ExtractedComments=800 Coverage=80.00% MorePlaceholders=125
```

## JSON vs JSONL

- JSON unico: um unico arquivo com um array gigante. Pior para append e para recuperar em caso de queda.
- JSONL: uma linha JSON por documento. Melhor para processamento incremental, reprocessamento parcial e pipelines de RAG.

Para crawler de longa duracao, JSONL e mais robusto.

