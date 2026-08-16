// Microphone capture and gapless playback for the voice relay.
//
// Rates are fixed by the model: 16kHz up, 24kHz down. The browser almost never
// records at 16kHz natively (48kHz is typical), so capture resamples on the way
// out. Playback asks the AudioContext for 24kHz directly and lets the platform
// handle the final conversion to the output device.

import { decodePcmChunk, encodePcmChunk, rms } from './pcm'

export const INPUT_SAMPLE_RATE = 16000
export const OUTPUT_SAMPLE_RATE = 24000

/** Linear resample. Cheap and good enough for speech at these ratios. */
export function resample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate || input.length === 0) return input
  const ratio = fromRate / toRate
  const outLength = Math.floor(input.length / ratio)
  const out = new Float32Array(outLength)
  for (let i = 0; i < outLength; i++) {
    const pos = i * ratio
    const left = Math.floor(pos)
    const right = Math.min(left + 1, input.length - 1)
    const frac = pos - left
    out[i] = input[left] * (1 - frac) + input[right] * frac
  }
  return out
}

type RecorderCallbacks = {
  onChunk: (base64Pcm: string) => void
  onLevel?: (level: number) => void
}

/** Mic -> 16kHz PCM chunks, base64, ready for the socket. */
export class MicRecorder {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null

  constructor(private callbacks: RecorderCallbacks) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })

    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext
    this.ctx = new AudioCtx()
    this.source = this.ctx.createMediaStreamSource(this.stream)

    // ScriptProcessor is deprecated in favour of AudioWorklet, but it needs no
    // separate module file to load and is supported everywhere this app runs.
    // Worth revisiting if capture ever shows up in a profile.
    this.node = this.ctx.createScriptProcessor(4096, 1, 1)
    this.node.onaudioprocess = (event) => {
      const samples = event.inputBuffer.getChannelData(0)
      this.callbacks.onLevel?.(rms(samples))
      const downsampled = resample(samples, this.ctx!.sampleRate, INPUT_SAMPLE_RATE)
      this.callbacks.onChunk(encodePcmChunk(downsampled))
    }

    this.source.connect(this.node)
    // ScriptProcessor only fires while connected to a destination. Routing it
    // through a muted gain node keeps it running without echoing the mic back
    // into the speakers.
    const mute = this.ctx.createGain()
    mute.gain.value = 0
    this.node.connect(mute)
    mute.connect(this.ctx.destination)
  }

  stop(): void {
    this.node?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())
    this.ctx?.close().catch(() => undefined)
    this.node = null
    this.source = null
    this.stream = null
    this.ctx = null
  }
}

/** Queues 24kHz PCM chunks so consecutive chunks play without a seam. */
export class PcmPlayer {
  private ctx: AudioContext | null = null
  private gain: GainNode | null = null
  private nextStartTime = 0
  private sources = new Set<AudioBufferSourceNode>()

  private ensureContext(): AudioContext {
    if (!this.ctx || this.ctx.state === 'closed') {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext
      this.ctx = new AudioCtx({ sampleRate: OUTPUT_SAMPLE_RATE })
      this.gain = this.ctx.createGain()
      this.gain.connect(this.ctx.destination)
      this.nextStartTime = 0
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume()
    return this.ctx
  }

  play(base64Pcm: string): void {
    const samples = decodePcmChunk(base64Pcm)
    if (samples.length === 0) return

    const ctx = this.ensureContext()
    const buffer = ctx.createBuffer(1, samples.length, OUTPUT_SAMPLE_RATE)
    // set() rather than copyToChannel(): the latter's signature pins the
    // Float32Array to an ArrayBuffer backing store, which decoded frames do not
    // statically satisfy.
    buffer.getChannelData(0).set(samples)

    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.gain!)

    // Schedule against the running clock, not "now": chunks arrive faster than
    // real time, so starting each at currentTime would overlap them.
    const startAt = Math.max(ctx.currentTime, this.nextStartTime)
    source.start(startAt)
    this.nextStartTime = startAt + buffer.duration

    this.sources.add(source)
    source.onended = () => this.sources.delete(source)
  }

  /** Drop anything still queued — used when the model is interrupted. */
  interrupt(): void {
    this.sources.forEach((s) => {
      try {
        s.stop()
      } catch {
        /* already ended */
      }
    })
    this.sources.clear()
    this.nextStartTime = this.ctx?.currentTime ?? 0
  }

  stop(): void {
    this.interrupt()
    this.ctx?.close().catch(() => undefined)
    this.ctx = null
    this.gain = null
  }
}
