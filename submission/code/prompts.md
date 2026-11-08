# Prompts

Generator: qwen3:8b, temperature 0, thinking off, at most 200 tokens. Retrieval: embeddinggemma, top 3, prefixes `task: search result | query: ` / `title: none | text: `.

## Final prompt

```
Answer the question using only the passages below. Cite the passage ids you used in square brackets, like [bus.txt#4].
If the passages do not answer the question, reply exactly: Not in the documents.
Answer in one or two sentences.

Passages:
{passages}

Question: {question}
Answer:
```

Four testable rules: only the passages; citations in the fixed `[file.txt#n]` form, with an example id in exactly the format of the ids in the context; an exact refusal line (checked by equality); a length limit.

## Discarded variant

```
Answer the question using only the passages below. Cite the passage ids you used in square brackets, like [bus.txt#4].
If you are not sure, say so.
Answer in one or two sentences.

Passages:
{passages}

Question: {question}
Answer:
```

Tested on the 3 no-answer questions with the same retrieved passages. Exact refusals: final prompt 3/3, discarded variant 0/3.

| question | final prompt | discarded variant |
|---|---|---|
| Q9: What are my rights if my ferry is cancelled? | Not in the documents. | The passages provided do not mention ferry passenger rights. Therefore, I cannot provide specific information based on the given texts. |
| Q10: Do I get compensation if my taxi to the airport is late? | Not in the documents. | If your taxi to the airport is late and it results in a delay of 3 hours or more at your final destination, you may be entitled to compensation, provided the delay was not due to extraordinary circumstances [air.txt#12]. However, the passage does not specifically address delays caused by taxi services [air.txt#38]. |
| Q11: How much alcohol can I bring back tax-free from another EU country? | Not in the documents. | The provided passages do not mention anything about the amount of alcohol that can be brought back tax-free from another EU country. Therefore, I cannot answer this question based on the given information. |

**Reason for discarding:** the variant gave the exact refusal line 0/3 times. On Q10 it asserted that a late taxi could lead to compensation and cited air.txt#12, which is a false answer; on Q9 and Q11 it refused in its own words, which an equality check cannot detect.
