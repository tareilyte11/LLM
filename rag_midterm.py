
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import requests

# ---------------------------------------------------------------- settings
DOCS = Path("my_documents")
QUESTIONS_FILE = Path("youreurope_queries.json")
OUT = Path("submission")
LOG = Path("run_log.json")

OLLAMA = "http://localhost:11434"
EMBED, GEN = "embeddinggemma", "qwen3:8b"
PREFIX = {"query": "task: search result | query: ", "doc": "title: none | text: "}  # model card; Ollama adds nothing
TOP_K = 3
TEMPERATURE = 0

DROP_IDS = set()

REFUSAL = "Not in the documents"

PROMPT = """Answer the question using only the passages below. Cite the passage ids you used in square brackets, like [bus.txt#4].
If the passages do not answer the question, reply exactly: Not in the documents.
Answer in one or two sentences.

Passages:
{passages}

Question: {question}
Answer:"""

# The variant we tried and dropped: no exact refusal line, only a soft instruction.
PROMPT_DISCARDED = """Answer the question using only the passages below. Cite the passage ids you used in square brackets, like [bus.txt#4].
If you are not sure, say so.
Answer in one or two sentences.

Passages:
{passages}

Question: {question}
Answer:"""

# ------------------------------------------------- fill in after run 1 (read review.md)
# question id: ("verdict", "one evidence sentence naming the chunk or phrase that decides it")
# verdicts: correct, retrieval_failure, wrong_refusal, generation_failure,
#           citation_failure, correct_refusal, false_answer
JUDGEMENTS = {
    1: ("retrieval_failure", "rail.txt#26 (25%/50% delay compensation) is not in the top 3; the near-duplicate rail.txt#36 (missed connection) was retrieved instead, and the answer also misreads it, giving 50% for a 90-minute delay where #36 says 25%."),
    2: ("correct", "roaming.txt#1 says calls, texts and data in another EU country are charged at domestic rates; the answer says no and cites #1."),
    3: ("correct", "healthcare.txt#9 says the EHIC does not cover rescue and repatriation; the answer says no and cites #9."),
    4: ("correct", "air.txt#35 gives 250 EUR for flights of 1 500 km or less delayed 3 hours or more; the answer says 250 EUR and cites #35."),
    5: ("correct", "delivery.txt#7 says the duty is EUR 3 per item and not per parcel; the answer matches and cites #7."),
    6: ("correct", "returns.txt#3 lists concert tickets as an exception to the 14-day cooling-off period; the answer says no and cites #3, not the general rule in #1."),
    7: ("correct", "bus.txt#4 says to complain within 3 months of the scheduled service; the answer matches and cites #4."),
    8: ("generation_failure", "Only package.txt#13 of the four gold chunks was retrieved; the answer treats 'trip' as a package holiday and never says that flights, trains and buses have different rules."),
    9: ("correct_refusal", "Exact refusal line, nothing cited; the retrieved bus and air cancellation chunks were not used."),
    10: ("correct_refusal", "Exact refusal line, nothing cited; the retrieved air delay chunks were not used."),
    11: ("correct_refusal", "Exact refusal line, nothing cited; the EUR 3 customs duty chunks were not used."),
}

CORPUS_DESCRIPTION = ("Ten Your Europe pages (European Commission, CC BY 4.0) on passenger rights "
                      "(air, rail, bus, package travel), shopping (guarantees, returns, delivery), roaming, "
                      "health cover abroad and consumer dispute resolution; downloaded November 2026.")


# ---------------------------------------------------------------- pipeline
def read_documents(folder):
    """One record per paragraph: id = <file>#<n>, the address the answer will cite."""
    chunks = []
    for f in sorted(Path(folder).glob("*.txt")):
        paras = [p.strip() for p in re.split(r"\n\s*\n", f.read_text(encoding="utf-8")) if p.strip()]
        chunks += [{"id": f"{f.name}#{i}", "document": f.name, "lang": "en", "text": p}
                   for i, p in enumerate(paras, start=1)]
    return chunks


def embed(texts, kind):
    r = requests.post(f"{OLLAMA}/api/embed", timeout=600,
                      json={"model": EMBED, "input": [PREFIX[kind] + t for t in texts], "truncate": False})
    r.raise_for_status()
    v = np.array(r.json()["embeddings"], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def generate(prompt):
    r = requests.post(f"{OLLAMA}/api/generate", timeout=600,
                      json={"model": GEN, "prompt": prompt, "stream": False, "think": False,
                            "options": {"temperature": TEMPERATURE, "num_predict": 200}})
    r.raise_for_status()
    answer = re.sub(r"<think>.*?</think>", "", r.json()["response"], flags=re.S).strip()
    return answer


def citations(answer):
    return sorted(set(re.findall(r"\[([a-z]+\.txt#\d+)\]", answer)))


def is_refusal(answer):
    return answer.strip().strip(".").lower() == REFUSAL.lower()


def auto_hint(q, retrieved, answer, cites):
    """What the log alone decides; the rest needs your judgement. Shown in review.md only."""
    gold = set(q["gold_chunks"])
    if not gold:
        return "correct_refusal" if is_refusal(answer) and not cites else "false_answer"
    if not gold & set(retrieved):
        return "retrieval_failure"
    if is_refusal(answer):
        return "wrong_refusal"
    note = "read the answer: correct or generation_failure"
    if not cites or any(c not in gold for c in cites):
        note += " (cites a non-gold chunk or nothing: citation_failure if the answer is right)"
    return note


def run_models(chunks, questions):
    by_id = {c["id"]: c for c in chunks}
    E = embed([c["text"] for c in chunks], "doc")
    rows, discarded = [], []
    for q in questions:
        scores = E @ embed([q["question"]], "query")[0]
        order = np.argsort(-scores)
        retrieved = [chunks[i]["id"] for i in order[:TOP_K]]
        ranking = [[chunks[i]["id"], round(float(scores[i]), 4)] for i in order[:10]]
        passages = "\n".join(f"[{cid}] {by_id[cid]['text']}" for cid in retrieved)
        answer = generate(PROMPT.format(passages=passages, question=q["question"]))
        rows.append({"id": q["id"], "retrieved": retrieved, "top10": ranking, "answer": answer})
        print(f"Q{q['id']} ({q['kind']}): {q['question']}\n   gold {q['gold_chunks']}  retrieved {retrieved}\n   A: {answer}\n")
        if not q["gold_chunks"]:   # the discarded prompt, on the questions it is meant to handle
            alt = generate(PROMPT_DISCARDED.format(passages=passages, question=q["question"]))
            discarded.append({"id": q["id"], "question": q["question"], "answer": alt})
    return {"rows": rows, "discarded": discarded}


def load_questions():
    data = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))["queries"]
    return [{"id": q["id"], "question": q["query"], "lang": q["lang"], "kind": q["category"],
             "gold_chunks": q["gold_chunks"], "gold_answer": q["gold_answer"]}
            for q in data if q["id"] not in DROP_IDS]


def main():
    chunks = read_documents(DOCS)
    questions = load_questions()
    by_id = {c["id"]: c for c in chunks}
    known = set(by_id)
    for q in questions:   # a gold id that does not exist would make every verdict meaningless
        missing = [g for g in q["gold_chunks"] if g not in known]
        if missing:
            raise SystemExit(f"Q{q['id']}: gold chunk(s) {missing} not in my_documents/ - rebuild or fix the ids")
    print(f"{len(chunks)} chunks from {len({c['document'] for c in chunks})} files; {len(questions)} questions")

    if LOG.exists():
        log = json.loads(LOG.read_text(encoding="utf-8"))
        print(f"reusing {LOG} (no model calls); delete it to run the models again\n")
    else:
        log = run_models(chunks, questions)
        LOG.write_text(json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8")

    rows = {r["id"]: r for r in log["rows"]}
    results, review = [], ["# Review: judge each answer, then fill in JUDGEMENTS in rag_midterm.py\n"]
    for q in questions:
        r = rows[q["id"]]
        cites = citations(r["answer"])
        verdict, evidence = JUDGEMENTS.get(q["id"], (None, ""))
        results.append(dict(q, written_before_running=True, retrieved=r["retrieved"], answer=r["answer"],
                            citations=cites, verdict=verdict, evidence=evidence))
        review.append(f"## Q{q['id']} ({q['kind']}): {q['question']}\n")
        review.append(f"- **gold chunks:** {q['gold_chunks'] or 'none (no answer in the corpus)'}")
        review.append(f"- **gold answer:** {q['gold_answer'] or '-'}")
        review.append(f"- **retrieved (top {TOP_K}):** {r['retrieved']}")
        review.append(f"- **top 10 with scores:** {r['top10']}")
        review.append(f"- **answer:** {r['answer']}")
        review.append(f"- **citations:** {cites}")
        review.append(f"- **what the log decides:** {auto_hint(q, r['retrieved'], r['answer'], cites)}")
        review.append(f"- **your verdict so far:** {verdict or '(not judged yet)'}\n")
        review.append("Retrieved chunk texts:\n")
        review += [f"> **[{cid}]** {by_id[cid]['text']}\n" for cid in r["retrieved"]]

    # submission files
    (OUT / "corpus").mkdir(parents=True, exist_ok=True)
    (OUT / "code").mkdir(parents=True, exist_ok=True)
    (OUT / "corpus" / "chunks.json").write_text(json.dumps(chunks, indent=1, ensure_ascii=False), encoding="utf-8")
    answers = {
        "corpus": {"type": "own", "description": CORPUS_DESCRIPTION,
                   "documents": len({c["document"] for c in chunks}),
                   "chunking": {"unit": "paragraph", "size": 1, "overlap": 0,
                                "rule": "one paragraph per chunk, split on blank lines; built by build_documents.py "
                                        "(page title and section heading prepended, blocks over 130 words split "
                                        "at sentence boundaries)"}},
        "retrieval": {"system": "dense", "model": EMBED, "top_k": TOP_K},
        "generation": {"model": GEN, "temperature": TEMPERATURE, "prompt_file": "code/prompts.md"},
        "questions": results,
    }
    (OUT / "answers.json").write_text(json.dumps(answers, indent=1, ensure_ascii=False), encoding="utf-8")

    disc = log["discarded"]
    final_refusals = sum(is_refusal(rows[d["id"]]["answer"]) for d in disc)
    alt_refusals = sum(is_refusal(d["answer"]) for d in disc)
    md = ["# Prompts\n",
          f"Generator: {GEN}, temperature {TEMPERATURE}, thinking off, at most 200 tokens. "
          f"Retrieval: {EMBED}, top {TOP_K}, prefixes `{PREFIX['query']}` / `{PREFIX['doc']}`.\n",
          "## Final prompt\n", "```", PROMPT, "```\n",
          "Four testable rules: only the passages; citations in the fixed `[file.txt#n]` form, with an example id "
          "in exactly the format of the ids in the context; an exact refusal line (checked by equality); "
          "a length limit.\n",
          "## Discarded variant\n", "```", PROMPT_DISCARDED, "```\n",
          f"Tested on the {len(disc)} no-answer questions with the same retrieved passages. "
          f"Exact refusals: final prompt {final_refusals}/{len(disc)}, discarded variant {alt_refusals}/{len(disc)}.\n",
          "| question | final prompt | discarded variant |", "|---|---|---|"]
    md += [f"| Q{d['id']}: {d['question']} | {rows[d['id']]['answer']} | {d['answer']} |" for d in disc]
    md += ["", "**Reason for discarding:** without an exact refusal line, a no-answer reply cannot be checked "
               "by equality, and a hedge that still asserts something counts as a false answer. "
               "*(Edit this sentence after reading the table above.)*\n"]
    (OUT / "code" / "prompts.md").write_text("\n".join(md), encoding="utf-8")
    shutil.copy(__file__, OUT / "code" / Path(__file__).name)
    Path("review.md").write_text("\n".join(review), encoding="utf-8")

    judged = Counter(r["verdict"] for r in results if r["verdict"])
    print(f"wrote {OUT}/corpus/chunks.json, {OUT}/answers.json, {OUT}/code/prompts.md and review.md")
    print(f"judged {sum(judged.values())}/{len(results)}: {dict(judged)}")
    if sum(judged.values()) < len(results):
        print("next: read review.md, fill in JUDGEMENTS, run again")


if __name__ == "__main__":
    main()
