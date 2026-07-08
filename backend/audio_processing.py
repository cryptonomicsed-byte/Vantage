"""Audio processing pipeline — BPM, key, waveform extraction."""
import os, json, subprocess, tempfile

def process_audio(file_path: str) -> dict:
    """Extract BPM, musical key, waveform peaks, and duration from an audio file."""
    result = {"bpm": 0, "key": "", "waveform_data": "[]", "duration_sec": 0.0}

    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(file_path, sr=None, duration=120)
        result["duration_sec"] = float(librosa.get_duration(y=y, sr=sr))

        # BPM
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        if hasattr(tempo, 'item'):
            result["bpm"] = int(round(float(tempo.item())))
        else:
            result["bpm"] = int(round(float(tempo)))

        # Musical key
        try:
            chroma = librosa.feature.chroma_stft(y=y, sr=sr)
            chroma_mean = chroma.mean(axis=1)
            key_idx = int(np.argmax(chroma_mean))
            keys_major = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
            keys_minor = ["Am", "A#m", "Bm", "Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m"]
            # Simple heuristic: if the relative minor has more energy, it's minor
            minor_idx = (key_idx + 3) % 12
            result["key"] = keys_minor[minor_idx] if chroma_mean[minor_idx] > chroma_mean[key_idx] * 0.8 else keys_major[key_idx]
        except:
            result["key"] = ""

        # Waveform peaks (1000 points)
        if len(y.shape) > 1:
            y = y.mean(axis=1)
        n_samples = len(y)
        step = max(1, n_samples // 1000)
        peaks = []
        for i in range(0, n_samples, step):
            chunk = np.abs(y[i:i+step])
            peaks.append(float(np.max(chunk)))
        # Normalize
        max_peak = max(peaks) if peaks else 1
        peaks = [p / max_peak for p in peaks]
        result["waveform_data"] = json.dumps(peaks[:1000])

    except ImportError:
        # Fallback: ffmpeg for duration only
        try:
            out = subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                stderr=subprocess.DEVNULL
            )
            result["duration_sec"] = float(out.decode().strip())
        except:
            pass

    return result
