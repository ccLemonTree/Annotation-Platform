# Retrieval match AI review implementation plan

**Goal:** Independently judge a query/candidate with a vision LLM; agreement with the human label approves, disagreement/uncertainty/failure requires manual review.

**Architecture:** Reuse durable ProjectWorkJob/Item, one job per query (existing unique job/asset key). New match-review service uses per-hit human/review baselines so completing one hit cannot invalidate unrelated hits. Persist AI evidence in both item result and query hit; immutable export snapshots stay unchanged. No database migration or remote training operations.

**Tech Stack:** Existing FastAPI/SQLAlchemy, httpx OpenAI chat client, React/Ant Design.

1. Add failing tests for independent text/image query requests, matching positive AND negative agreement, disagreements/uncertainty/invalid outputs, cancellation/retry and stale results after manual/model/image/scope changes.
2. Add editable match-review prompt and validated JSON verdict. Never send the human judgment/similarity to the LLM. Use at most query image + candidate image. Reuse existing server auth, timeout, image decoding/hashes and model snapshots.
3. Add administrator batch creation for all pending relationships or selected queries; reject active duplicates/foreign inputs. Worker results update only unchanged hits. Manual decisions win; changed judgments clear old evidence. Disagreements remain pending and appear in the project's manual queue.
4. Add model/prompt controls and live batch counts in MatchReviews, plus AI conclusion/reason/source and manual-needed filter. Keep existing project work queue cancellation/retry and correct operation labels.
5. Default matching training exports require approval. Explicit include-pending can retain unreviewed relationships, but AI disagreements/failures must first be manually approved. Preserve existing fixed snapshots and trusted detection import behavior.
6. Run focused/regression tests, browser flows at desktop/mobile and production build/lint. Verify real LLM request using only owned temporary images if an enabled local model is available; clean fixtures, compare business records/hashes and running training. No bulk audit of business labels without the user's review action.
