import json
import string
import requests
import numpy as np
from rank_bm25 import BM25Okapi

# 1. Load the handbook
with open("handbook.json", encoding="utf-8") as f:
    handbook = json.load(f)

#takes the value stored under the key "passages"
passages = handbook["passages"]

# Keep the original IDs alongside the text.
passage_ids = [passage["id"] for passage in passages]
corpus = [passage["text"] for passage in passages]

# 2. Apply the same cleaning to passages and queries.
def tokenize(text):
    # Replace punctuation with spaces and convert to lowercase.
    translator = str.maketrans({
        character: " " for character in string.punctuation
    })
    return text.lower().translate(translator).split()


tokenized_corpus = [tokenize(doc) for doc in corpus]

# 3. Build the BM25 index.
bm25 = BM25Okapi(
    tokenized_corpus,
    k1=1.5,
    b=0.75,
    epsilon=0.25,
)


# 4. Retrieve the top 10 passages.
def bm25_search(query, top_k=10):
    scores = bm25.get_scores(tokenize(query))

    # Sort by score; tied scores keep the handbook's original order.
    ranked_indices = sorted(
        range(len(corpus)),
        key=lambda i: (-float(scores[i]), i),
    )[:top_k]

    return [
        {
            "passage_id": passage_ids[i],
            "score": float(scores[i]),
            "text": corpus[i],
        }
        for i in ranked_indices
    ]


# Load all public queries.
with open("queries.json", encoding="utf-8") as f:
    query_data = json.load(f)

queries = query_data["queries"]

rankings = []
evaluation = []

for item in queries:
    results = bm25_search(item["query"], top_k=10)
    retrieved_ids = [result["passage_id"] for result in results]
    gold_ids = set(item["gold"])

    rankings.append({
        "query_id": item["id"],
        "passage_ids": retrieved_ids,
    })

    if gold_ids:
        hit_at_1 = int(bool(gold_ids.intersection(retrieved_ids[:1])))
        hit_at_3 = int(bool(gold_ids.intersection(retrieved_ids[:3])))

        recall_at_10 = (
            len(gold_ids.intersection(retrieved_ids))
            / len(gold_ids)
        )
    else:
        # These retrieval metrics are excluded for unanswerable queries.
        hit_at_1 = None
        hit_at_3 = None
        recall_at_10 = None

    evaluation.append({
        "query_id": item["id"],
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "recall_at_10": recall_at_10,
    })

    print(f"\nQuestion {item['id']}: {item['query']}")
    print(f"Retrieved: {retrieved_ids}")
    print(f"Gold: {item['gold']}")

    if gold_ids:
        print(f"Hit@1: {hit_at_1}")
        print(f"Hit@3: {hit_at_3}")
        print(f"Recall@10: {recall_at_10:.2%}")
    else:
        print("No gold passages; excluded from these metric averages.")

# Average over questions that have at least one gold passage.
answerable = [
    row for row in evaluation
    if row["recall_at_10"] is not None
]

print(f"\nProcessed {len(queries)} questions.")
print(f"Questions included in averages: {len(answerable)}")

if answerable:
    for metric in ("hit_at_1", "hit_at_3", "recall_at_10"):
        average = sum(row[metric] for row in answerable) / len(answerable)
        print(f"Mean {metric}: {average:.2%}")


#Dense retrieval##############

MODEL = "embeddinggemma"
EMBED_URL = "http://localhost:11434/api/embed"


# 1. Load the handbook and questions.
with open("handbook.json", encoding="utf-8") as f:
    passages = json.load(f)["passages"]

with open("queries.json", encoding="utf-8") as f:
    queries = json.load(f)["queries"]


# 2. Get embeddings from Ollama.
def embed(texts):
    response = requests.post(
        EMBED_URL,
        json={
            "model": MODEL,
            "input": texts,
            "truncate": False,
        },
        timeout=300,
    )
    response.raise_for_status()

    vectors = np.array(
        response.json()["embeddings"],
        dtype=np.float32,
    )

    # Normalize vectors to length 1.
    lengths = np.linalg.norm(vectors, axis=1, keepdims=True)

    return vectors / lengths


# 3. Embed handbook passages
passage_vectors = []

texts = [passage["text"] for passage in passages]
passage_vectors = embed(texts)


# 4. Retrieve the most similar passages.
def dense_search(query, top_k=10):
    query_vector = embed([query])[0]

    # Dot product of normalized vectors = cosine similarity.
    scores = passage_vectors @ query_vector

    # Highest similarity first; ties follow handbook order.
    ranked_indices = sorted(
        range(len(passages)),
        key=lambda i: (-float(scores[i]), i),
    )[:top_k]

    return [
        {
            "passage_id": passages[i]["id"],
            "score": float(scores[i]),
            "text": passages[i]["text"],
        }
        for i in ranked_indices
    ]


# 5. Run all questions and calculate our local metrics.
rankings = []
evaluation = []

for item in queries:
    results = dense_search(item["query"])
    retrieved_ids = [result["passage_id"] for result in results]

    rankings.append({
        "query_id": item["id"],
        "passage_ids": retrieved_ids,
    })

    # Gold IDs are used only for evaluation.
    gold_ids = set(item["gold"])

    if gold_ids:
        hit_at_1 = int(bool(gold_ids.intersection(retrieved_ids[:1])))
        hit_at_3 = int(bool(gold_ids.intersection(retrieved_ids[:3])))
        recall_at_10 = (
            len(gold_ids.intersection(retrieved_ids)) / len(gold_ids)
        )
    else:
        hit_at_1 = None
        hit_at_3 = None
        recall_at_10 = None

    evaluation.append({
        "query_id": item["id"],
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "recall_at_10": recall_at_10,
    })

    print(f"\nQuestion {item['id']}: {item['query']}")
    print(f"Retrieved: {retrieved_ids}")
    print(f"Gold: {item['gold']}")

    if gold_ids:
        print(f"Hit@1: {hit_at_1}")
        print(f"Hit@3: {hit_at_3}")
        print(f"Recall@10: {recall_at_10:.2%}")
    else:
        print("No gold passages; excluded from metric averages.")


# 7. Print averages for questions with nonempty gold lists.
answerable = [
    row for row in evaluation
    if row["recall_at_10"] is not None
]

print(f"\nProcessed {len(queries)} questions.")
print(f"Questions included in averages: {len(answerable)}")

if answerable:
    for metric in ("hit_at_1", "hit_at_3", "recall_at_10"):
        average = sum(row[metric] for row in answerable) / len(answerable)
        print(f"Mean {metric}: {average:.2%}")


# Hybrid retrieval: BM25 + dense #################

def hybrid_search(query, top_k=10, rrf_k=15):
    # Combine full rankings, then select the best 10 combined results.
    bm25_results = bm25_search(query, top_k=len(passages))
    dense_results = dense_search(query, top_k=len(passages))

    combined_scores = {}

    for results in (bm25_results, dense_results):
        for rank, result in enumerate(results, start=1):
            passage_id = result["passage_id"]
            combined_scores[passage_id] = (
                combined_scores.get(passage_id, 0.0)
                + 1.0 / (rrf_k + rank)
            )

    # Use original handbook order to break ties consistently.
    passage_order = {
        passage["id"]: i for i, passage in enumerate(passages)
    }
    passage_by_id = {
        passage["id"]: passage for passage in passages
    }

    ranked_ids = sorted(
        combined_scores,
        key=lambda passage_id: (
            -combined_scores[passage_id],
            passage_order[passage_id],
        ),
    )[:top_k]

    return [
        {
            "passage_id": passage_id,
            "score": combined_scores[passage_id],
            "text": passage_by_id[passage_id]["text"],
        }
        for passage_id in ranked_ids
    ]

hybrid_rankings = []
hybrid_evaluation = []

for item in queries:
    results = hybrid_search(item["query"], top_k=10)
    retrieved_ids = [result["passage_id"] for result in results]

    hybrid_rankings.append({
        "query_id": item["id"],
        "passage_ids": retrieved_ids,
    })

    gold_ids = set(item.get("gold", []))

    if gold_ids:
        hit_at_1 = int(bool(gold_ids.intersection(retrieved_ids[:1])))
        hit_at_3 = int(bool(gold_ids.intersection(retrieved_ids[:3])))
        recall_at_10 = len(gold_ids.intersection(retrieved_ids)) / len(gold_ids)
    else:
        hit_at_1 = None
        hit_at_3 = None
        recall_at_10 = None

    hybrid_evaluation.append({
        "query_id": item["id"],
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "recall_at_10": recall_at_10,
    })

    print(f"\nQuestion {item['id']}: {item['query']}")
    print(f"Retrieved: {retrieved_ids}")
    print(f"Gold: {item.get('gold', 'not supplied')}")

    if gold_ids:
        print(f"Hit@1: {hit_at_1}")
        print(f"Hit@3: {hit_at_3}")
        print(f"Recall@10: {recall_at_10:.2%}")
    else:
        print("Empty or missing gold; excluded from metric averages.")

with open("hybrid_rankings.json", "w", encoding="utf-8") as f:
    json.dump(hybrid_rankings, f, ensure_ascii=False, indent=2)

with open("hybrid_evaluation.json", "w", encoding="utf-8") as f:
    json.dump(hybrid_evaluation, f, ensure_ascii=False, indent=2)

answerable = [
    row for row in hybrid_evaluation if row["recall_at_10"] is not None
]

print(f"\nProcessed {len(queries)} questions with hybrid retrieval.")
print(f"Questions included in averages: {len(answerable)}")

if answerable:
    for metric in ("hit_at_1", "hit_at_3", "recall_at_10"):
        average = sum(row[metric] for row in answerable) / len(answerable)
        print(f"Mean {metric}: {average:.2%}")