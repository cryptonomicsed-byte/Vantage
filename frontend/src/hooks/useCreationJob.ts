import { useEffect, useRef, useState } from 'react'

export interface CreationJob {
  id: number
  prompt: string
  status: 'queued' | 'scripting' | 'voicing' | 'visualizing' | 'composing' | 'done' | 'error'
  script?: { title?: string; content?: string; tags?: string[] }
  audio_path?: string
  video_path?: string
  result_broadcast_id?: number
  error_text?: string
  created_at: string
  updated_at: string
}

export function useCreationJob(jobId: number | null, apiKey: string) {
  const [job, setJob] = useState<CreationJob | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!jobId || !apiKey) return

    function poll() {
      fetch(`/api/agents/me/creation-jobs/${jobId}`, {
        headers: { 'X-Agent-Key': apiKey },
      })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data) setJob(data)
          if (data?.status === 'done' || data?.status === 'error') {
            if (intervalRef.current) clearInterval(intervalRef.current)
          }
        })
        .catch(() => {})
    }

    poll()
    intervalRef.current = setInterval(poll, 4000)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [jobId, apiKey])

  return job
}
