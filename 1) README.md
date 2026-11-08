# EU consumer and passenger rights: a RAG prototype

Mid-term project for *Large Language Models*.
A retrieval-augmented question-answering system over ten Your Europe pages: it finds the relevant
paragraphs for a question, answers in one or two sentences with citations such as `[bus.txt#4]`,
and replies "Not in the documents." when the pages do not cover the question.

## Documents
 
Ten pages from the European Commission's [Your Europe](https://europa.eu/youreurope/) portal, reused
under CC BY 4.0 (Commission Decision 2011/833/EU). URLs, page dates and the changes made are in
[`SOURCES.md`](SOURCES.md).
 
| File | Topic |
|---|---|
| `air.txt`, `rail.txt`, `bus.txt` | passenger rights: delays, cancellations, compensation, complaints |
| `package.txt` | package travel and linked travel arrangements |
| `guarantees.txt`, `returns.txt`, `delivery.txt` | shopping: legal guarantee, 14-day withdrawal, delivery and customs duty |
| `roaming.txt` | using a mobile phone in another EU country |
| `healthcare.txt` | the European Health Insurance Card on temporary stays |
| `disputes.txt` | resolving consumer disputes out of court |
 
264 chunks: one paragraph per chunk, page title and section heading prepended, blocks over 130 words
split at sentence boundaries, navigation-only sections and duplicate fragments removed.
 
## How to run
 
Requires Python 3 and [Ollama](https://ollama.com) with two models:
 
```
pip install -r requirements.txt
ollama pull embeddinggemma
ollama pull qwen3:8b
```
 
1. `python build_documents.py`: downloads the ten pages and writes `my_documents/*.txt`,
   `youreurope_handbook.json` and `SOURCES.md`.
2. `python BM_25_new_europe-rights.py`: compares BM25, dense and hybrid retrieval on the questions
   in `youreurope_queries.json` (Hit@1, Hit@3, Recall@10). This is the "before" run.
   `python BM_25_new_europe-rights_with_controlled_change.py` is the same script with one change,
   stop-word removal in the BM25 tokenizer; this is the "after" run.
3. `python rag_midterm.py`: retrieves the top 3 chunks per question, generates the answers and writes
   the files in `submission/`. Run it a second time after filling in the verdicts; it reuses
   `run_log.json` instead of calling the model again (a copy of the logged run is in `results/`).
## Setup
 
| Parameter | Value |
|---|---|
| Embedding model | embeddinggemma (768 dimensions, vectors normalised to length 1) |
| Prefixes | query `task: search result \| query: `, document `title: none \| text: ` |
| Chunk unit | paragraph, at most 130 words, no overlap |
| Retrieval | dense, cosine similarity, top k = 3 |
| Generator | qwen3:8b, temperature 0, thinking off, at most 200 tokens |
| Prompt | [`submission/code/prompts.md`](submission/code/prompts.md) |
 
## Results
 
**Retrieval** (8 answerable questions, before the stop-word change):
 
| Method | Hit@1 | Hit@3 | Recall@10 |
|---|---|---|---|
| BM25 | 25.0% | 50.0% | 65.6% |
| Dense | 62.5% | 87.5% | 96.9% |
| Hybrid (RRF, k = 15) | 37.5% | 87.5% | 87.5% |
 
**Controlled change**: removing stop words in BM25. This removed spurious matches on the word "my",
which appears in only five chunks, all roaming headings. BM25 Recall@10 rose to 71.9%, but Hit@3 fell
to 37.5%: "long" now matches "long-distance", and "complain" does not match "complaint" without
stemming. Dense retrieval was unchanged, as expected.
 
**Answers** (11 questions, judged by hand): 5 correct, 3 correct refusals, 1 retrieval failure,
1 generation failure.
 
- *Retrieval failure (Q1):* the delay-compensation chunk was not retrieved; a near-duplicate chunk
  about missed connections, with the same percentages, came first. The answer also misread that chunk
  (50% instead of 25%) while citing it.
- *Generation failure (Q8, ambiguous):* the answer treated "trip" as a package holiday and did not
  mention that flights, trains and buses have different rules.
## Limitations
 
- Eleven questions and one generator; differences of one question move a metric by 10 to 12.5 points.
- The stop-word change was chosen after seeing the first retrieval results.
- The pages describe EU rules as of their "last checked" dates (January to October 2026);
  national rules and later changes are not covered.
