// PCM conversions for the voice relay.
//
// The wire carries raw little-endian 16-bit PCM as base64: 16kHz going up to
// the model, 24kHz coming back. Web Audio works in float32 at [-1, 1], so
// every frame crosses this boundary twice per turn. Kept separate from the
// recorder and player because it is the only part with arithmetic worth
// testing, and it tests without a browser.

/** Largest magnitude of a signed 16-bit sample. */
const INT16_PEAK = 0x8000 // 32768

export function floatTo16BitPCM(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    // Clamp before scaling: values outside [-1, 1] would otherwise wrap and
    // turn a loud passage into noise rather than clipping it.
    const clamped = Math.max(-1, Math.min(1, input[i]))
    out[i] = clamped < 0 ? clamped * INT16_PEAK : clamped * (INT16_PEAK - 1)
  }
  return out
}

export function int16ToFloat32(input: Int16Array): Float32Array {
  const out = new Float32Array(input.length)
  for (let i = 0; i < input.length; i++) out[i] = input[i] / INT16_PEAK
  return out
}

export function bytesToBase64(bytes: Uint8Array): string {
  // Chunked because String.fromCharCode(...arr) blows the argument limit on
  // anything larger than a few tens of KB, which a few seconds of audio is.
  let binary = ''
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

export function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64)
  const out = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i)
  return out
}

/** Float32 mic samples -> base64 16-bit PCM, ready to send. */
export function encodePcmChunk(samples: Float32Array): string {
  const pcm = floatTo16BitPCM(samples)
  return bytesToBase64(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength))
}

/** base64 16-bit PCM from the model -> float32 samples, ready to play. */
export function decodePcmChunk(b64: string): Float32Array {
  const bytes = base64ToBytes(b64)
  // A trailing odd byte cannot form a sample; drop it rather than reading past
  // the end and producing a garbage final sample.
  const usable = bytes.byteLength - (bytes.byteLength % 2)
  if (usable <= 0) return new Float32Array(0)
  const pcm = new Int16Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + usable))
  return int16ToFloat32(pcm)
}

/** Root-mean-square level of a frame, for the level meter. */
export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
  return Math.sqrt(sum / samples.length)
}
