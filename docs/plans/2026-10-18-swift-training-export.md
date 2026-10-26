# ms-swift training dataset exports

Use the supplied image training contract; retain portable exports and project crop scope.

1. Pure conversion tests for SFT, InfoNCE, contrastive/online_contrastive and cosine_similarity; independent modality alignment, one positive per row, explicit negative judgments.
2. Reuse the current ZIP builder and its image hashes/annotation approval/provenance filters. Add train.jsonl, format/source/count metadata and usage instructions. No schema migration, embedding calls or training launches.
3. Export UI chooses format, description/label/matching source and editable SFT instruction. Default portable format remains compatible. Surface server validation details for empty/invalid samples.
4. Verify API ZIPs with decoded packaged images, browser selection/download/errors at desktop/mobile, build/lint, and the live backend using only owned temporary project data. Confirm business rows/files and running training are preserved.

Label policy: existing correct/incorrect judgments become 1/0; cosine uses 1.0/0.0. Never use retrieval similarity as a supervised label. Description/label pairs supply positives only. InfoNCE splits multiple positives, copies only explicit hard negatives, and omits unknowns. No invented continuous ratings or automatic train/validation splitting. Video/audio and detection grounding are outside the current image-description export flow.
