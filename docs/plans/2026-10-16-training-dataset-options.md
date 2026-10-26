# Training dataset options

Goal: Generate detection configs, select classes, remember weights per tenant/server/recipe, and default timestamped output directories.

1. Add failing dataset and API tests: YAML paths from a different container, YOLO/RF layouts, class remapping, COCO filtering, unchanged input, saved defaults isolation, output directory creation.
2. Add one detection dataset helper, reused by CPU metadata inspection and generated training scripts. Keep source read-only; generate per-run config/labels, retain split boundaries and negative images. Use existing class metadata; refuse unknown classes and missing train/val.
3. Extend existing training schemas/routes/services (no migration), add label selection and remembered defaults to TrainingPage, explain /dev/shm, default output_root to /ai/chen/training/runs/<Shanghai timestamp>.
4. Run focused backend tests, frontend build/lint, real image CPU dataset loading against both supplied YAMLs. Verify active jobs before backend reload and verify live APIs. Never start GPU training.

Scope: services/training_dataset.py, training_config.py, training_executor.py, training_runs.py, schemas/training_run.py, api/training_run_routes.py, frontend training API/page, tests and usage docs. Existing files, model weights, datasets, training jobs and online GPU workloads must remain untouched.

User correction: server data must use its existing config without preparation or class filtering. Only platform snapshots automatically generate config and permit class selection. Regressions must execute generated server scripts with stub models to prove original path passthrough, reject server selected_labels, and keep snapshot generation/remapping tests. Native framework precheck must use exactly the same server config rules as training. Preserve weight defaults and timestamp outputs.
