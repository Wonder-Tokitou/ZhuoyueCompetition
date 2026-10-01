/**
 * Produce a compact ASCII idempotency key from UTF-8 JSON input.
 * This is intentionally not a security hash; it only deduplicates equivalent
 * UI submissions. Encoding to bytes first keeps Chinese and emoji safe.
 */
export function stableIdempotencyKey(scope, value) {
  const bytes = new TextEncoder().encode(JSON.stringify(value))
  let left = 0x811c9dc5
  let right = 0x9e3779b9
  for (let index = 0; index < bytes.length; index += 1) {
    const byte = bytes[index]
    left = Math.imul(left ^ byte, 0x01000193) >>> 0
    right = Math.imul(right ^ ((byte + index) & 0xff), 0x85ebca6b) >>> 0
    right ^= right >>> 13
  }
  const digest = left.toString(16).padStart(8, '0') + right.toString(16).padStart(8, '0')
  return `${scope}-${bytes.length.toString(36)}-${digest}`
}
