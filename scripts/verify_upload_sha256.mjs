import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { sha256 } from '../frontend/src/utils/sha256.ts';

const native = globalThis.crypto;
let checks = 0;
for (const webCrypto of [native, {}, undefined]) {
  Object.defineProperty(globalThis, 'crypto', { value: webCrypto, configurable: true });
  for (const size of [0, 1, 3, 55, 56, 63, 64, 65, 119, 120, 127, 128, 1000, 8388608]) {
    const bytes = randomBytes(size);
    const input = Uint8Array.from(bytes).buffer;
    assert.equal(await sha256(input), createHash('sha256').update(bytes).digest('hex'), `size=${size}, native=${!!webCrypto?.subtle}`);
    checks++;
  }
}
Object.defineProperty(globalThis, 'crypto', { value: native, configurable: true });
console.log(`${checks} SHA-256 checks passed, including insecure context and 8 MiB chunks`);
