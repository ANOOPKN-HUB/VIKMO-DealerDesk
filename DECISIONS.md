# Technical decisions — AI/ML track

The answers below address the eight AI/ML questions in the assignment. File references point to the implementation in this repository.

## 1. Schema, relationships, and deletion

`Product` stores the catalogue row and current stock; `Dealer` stores dealer identity; `Order` belongs to one `Dealer`; `OrderItem` belongs to an `Order` and references one `Product`; `IdempotencyRecord` associates a unique request key and body hash with an `Order` (`core/models.py:2-32`). `Order.dealer` uses `PROTECT` so an order's customer history cannot be deleted. `OrderItem.order` uses `CASCADE` because a line has no life without its order. `OrderItem.product` uses `PROTECT` so product history cannot disappear while referenced by an order line. `IdempotencyRecord.order` uses `CASCADE` because the request record has no purpose after its order is deleted. Product and dealer identifiers are unique; order item prices and totals are snapshots, so later catalogue price changes do not rewrite old orders.

## 2. Retrieval, embeddings, indexing, and ranking

I did not add embeddings or a vector index. For this 600-product catalogue I chose deterministic lexical retrieval because it is simple to inspect and needs no embedding service; I have not measured this choice against a labeled retrieval set. The code normalizes terms, removes stop words, carries vehicle context, filters candidate products with `icontains`, and ranks up to 200 candidates with explicit weights: fitment 5, name 4, category 3, SKU 2, and brand 1 (`core/assistant.py:29-45,48-85,88-132`). SKU has its unique database index, but there is no full-text or vector index. Exact fitment matching helps avoid mixing vehicle models. Synonyms, misspellings, and paraphrases can still fail; recall@k should be measured before choosing a future embedding or indexing strategy.

## 3. Tool-calling loop

`assistant_chat` passes the request to `run_assistant` (`core/views_extra.py:5-10`). The assistant declares `search_parts`, `check_stock`, and `create_draft_order`, then validates arguments and dispatches each call to server-side code (`core/assistant.py:188-224`). With a Gemini key, it sends the conversation, tool declarations, and automatic function-calling configuration to the configured model; each returned function call runs locally, and its result is sent back to Gemini for the next turn (`core/assistant.py:247-299`). The loop allows up to four model turns; HTTP retries for transient errors are separate (`core/assistant.py:14-27,259-299`). If the key is absent or the provider fails, the app uses and labels a local database fallback (`core/assistant.py:313-317`).

## 4. Price and stock grounding

`check_stock` fetches each requested SKU from `Product` and serializes its database price and stock (`core/assistant.py:206-214`). When a stock tool result exists, `_ground_tool_results` constructs the answer from those returned rows; the model's free-form response is not used for those figures (`core/assistant.py:147-168,275-312`). This grounds claims to the database values at query time. It does not lock inventory through delivery of the answer, so stock may change after the lookup; an order still needs its own transactional stock check.

## 5. Untrusted supplier text and prompt injection

The system instruction labels catalogue descriptions as untrusted and directs the model to use tools, never invent price or stock, ask for fitment clarification, and create drafts only (`core/assistant.py:184-186,260`). More importantly, the `search_parts` result sent to Gemini contains only SKU, name, brand, and fitment; it does not return the supplier description (`core/assistant.py:199-205`). Tool names and arguments are handled by server code with type and size checks; unknown tools return an error (`core/assistant.py:195-224`). This narrows exposure but is not a complete prompt-injection boundary: name, brand, or fitment text is also supplier-controlled, and instructions cannot guarantee model behavior. Keep authorization, stock checks, and order side effects enforced in code.

## 6. Forecast approach and validation

The forecaster compares last value, recent eight-week mean, same-week-last-year, a 60/40 recent/seasonal blend, and an event-adjusted blend (`ml/forecast_model.py:67-70,92-113,116-122`). Before each cutoff it includes only history dated on or before that cutoff; the 364-day seasonal row is also checked against the cutoff (`ml/forecast_model.py:63-65,92-96`). Rolling origins reserve earlier folds for method selection and later folds for validation (`ml/forecast_model.py:125-166`). On three held-out origins (2026-03-16, 2026-05-11, 2026-07-06; 456 SKU-weeks), the selected event-adjusted blend scored WMAPE 0.2471 and MAE 8.393, versus recent average 0.2480 / 8.424, seasonal-naive 0.3248 / 11.035, and last-value 0.3547 / 12.050. Its gain over recent average is small, and three origins provide limited evidence.

## 7. Weakest SKUs, promotions, Diwali, rare demand, and new SKUs

The evaluator reports aggregate metrics, not errors by SKU, so I cannot identify specific worst SKUs from the current output. I expect sparse or intermittent-demand SKUs to be difficult because there is no dedicated intermittent-demand model; this is an inference, not a measured SKU ranking. Promotions use a planned per-SKU future flag, but the uplift ratio is estimated globally from pre-cutoff promo versus regular sales, requires at least eight observations in each group, and is clamped to 1.0–1.5. A forecast week 7–35 days before a Diwali date gets a fixed 5% uplift (`ml/forecast_model.py:73-90,104-110`). Rarely sold SKUs use the mean of their most recent observed weeks and the seasonal value when available; otherwise seasonal falls back to the recent mean. A SKU with no sales history therefore forecasts zero, and there is no cross-SKU or product-analogue cold-start estimate (`ml/forecast_model.py:67-70,92-96`). The output covers SKUs in the lead-time file (`ml/forecast_model.py:169-180`). These are known weaknesses, not purchasing guarantees.

## 8. Weakest area and two-day improvement plan

The weakest area is evidence quality: lexical retrieval has no labeled recall evaluation, forecast validation has only three held-out origins and no per-SKU error report, and a successful live Gemini tool call still needs to be confirmed when provider access is available. With two more days, I would build a representative assistant query set and report retrieval/tool-call outcomes, add per-SKU forecast errors and compare an intermittent-demand baseline on additional rolling origins, then verify a live Gemini call and record the required five-minute walkthrough. I would keep changes that improve the measured results rather than claim improvements in advance.

## Verification record

- The recorded PostgreSQL-backed test run passed 21 tests, including assistant grounding/provider retries/fallback, forecast validation, and supplier sync (`PROJECT_AUDIT.md`).
- The recorded isolated API smoke run passed 10/10 checks. The smoke test creates an order; use a disposable database when running it (`PROJECT_AUDIT.md`).
- The isolated database migration and seed process was run twice without duplicates; it produced 600 products and 25 dealers (`PROJECT_AUDIT.md`).
- `python ml/forecast_model.py --evaluate` produces the rolling-origin comparison; `python ml/forecast_model.py --write` regenerates `ml/forecast.csv`.
- A real Gemini call and the video walkthrough remain to be verified/recorded before submission. Provider fallback is not evidence of live tool calling.
