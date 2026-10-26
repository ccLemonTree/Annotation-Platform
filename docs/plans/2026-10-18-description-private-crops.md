# Description and project-private crop correction

Goal: Direct image descriptions require no embeddings; only text/image retrieval uses Milvus. Crops belong to one project. A project may annotate crops only, excluding all originals including no-target originals.

1. Reproduce library crop registration and missing annotation scope in isolated tests.
2. Store new crops as Asset(image_id=None), with project paths and provenance; preserve IDs/annotations when detaching legacy crop assets from LibraryImage in migration022. Hide/block legacy crop library records without deleting original files or historical semantic records.
3. Persist annotation_scope=all|crops in existing project JSON settings. Apply the same eligibility helper to queues/neighbors, annotation writes, caption/index jobs, search candidates/judgments and training exports. Management can still list originals for crop preparation.
4. Vector workspace offers direct description (default), text retrieval and image retrieval. Reuse existing image editor; descriptions do not fetch embedding models/indices. Expose scope selection and original/crop filtering.
5. Red/green focused tests, disposable PostgreSQL migration, full backend checks, build/lint and real-browser flow checks. Back up before activating migration; preserve training and current business annotations, do not clear vectors or delete originals.
