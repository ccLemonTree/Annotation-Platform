# Cross-server model export implementation plan

**Goal:** Export ONNX on the original training server, transfer verified ONNX to a selected GPU server, build TensorRT, and download artifacts.

**Architecture:** Keep one existing durable TrainingRun. Store the ONNX stage server/config in its config and current phase in inspection. Reuse the training executor, GPU reservations, SSH-manager bridge and artifact downloads. Old single-server jobs and LoRA merge remain compatible.

**Tech Stack:** FastAPI, SQLAlchemy, Python, ssh-manager MCP, React/Ant Design.

1. Add isolated tests in `backend/backend/tests/test_model_output.py`: source/target routing, GPU reservations, stage transitions, stop/failure, bundle integrity and downloads. Run them before implementation to reproduce the missing flow.
2. Extend `schemas/model_output.py`, `services/model_output.py` and `services/training_runs.py`: ONNX always uses the training server; TensorRT uses a persisted two-stage configuration and phase, with both GPUs reserved until completion/confirmed stop.
3. Extend `services/training_executor.py`: package only completed ONNX artifacts with their external tensors, verify transfer checksum and all file checksums before extracting under the owned target task directory. Relay the bundle through a temporary local file using SSH-manager; never transfer source checkpoints or credentials between servers.
4. Update `frontend/src/components/ModelOutputForm.tsx`, API types and TrainingPage phase display: distinct source GPU/target server/GPU, preserve source path when switching destination, existing artifact download controls.
5. Run export/lifecycle/transfer regressions, frontend build, and isolated browser flow. Check live tasks before any backend reload. Do not export business weights or interrupt active workloads for testing.
