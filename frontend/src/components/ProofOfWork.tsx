/**
 * ProofOfWork — Cryptographic proof chain card for an artifact + receipt.
 *
 * Full mode: full card with all fields.
 * Compact mode: single-row summary (agent, type, hash, verified badge).
 */
import React from 'react'

// ── types ──────────────────────────────────────────────────────────────────────

interface ArtifactData {
  artifact_id: string
  task_id?: string
  agent_id?: number
  agent_name?: string
  type: string
  uri?: string
  hash?: string
  created_at: string
  metadata?: Record<string, unknown>
}

interface ReceiptData {
  receipt_id: string
  agent_name?: string
  capability?: string
  verified?: boolean
  receipt_hash?: string
}

export interface ProofOfWorkProps {
  artifact?: ArtifactData
  receipt?: ReceiptData
  compact?: boolean
}

// ── helpers ────────────────────────────────────────────────────────────────────

function trunc(s: string | undefined, max = 16): string {
  if (!s) return '—'
  return s.length > max ? s.slice(0, max) + '…' : s
}

function fmtDate(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toISOString().replace('T', ' ').slice(0, 16)
  } catch {
    return iso
  }
}

function fmtTaskId(id?: string): string {
  if (!id) return '—'
  // Already formatted like TASK-00291, or raw UUID — show as-is (truncated)
  return id.length > 20 ? id.slice(0, 20) + '…' : id
}

// ── Verified badge ─────────────────────────────────────────────────────────────

function VerifiedBadge({ verified }: { verified?: boolean }) {
  if (verified === true) {
    return (
      <span
        style={{
          fontSize: 10,
          padding: '2px 7px',
          borderRadius: 4,
          background: 'rgba(60,200,120,0.15)',
          color: '#3cc878',
          fontWeight: 700,
          letterSpacing: '0.05em',
          border: '1px solid rgba(60,200,120,0.3)',
          flexShrink: 0,
        }}
      >
        VERIFIED ✓
      </span>
    )
  }
  if (verified === false) {
    return (
      <span
        style={{
          fontSize: 10,
          padding: '2px 7px',
          borderRadius: 4,
          background: 'rgba(239,68,68,0.12)',
          color: '#ef4444',
          fontWeight: 700,
          letterSpacing: '0.05em',
          border: '1px solid rgba(239,68,68,0.25)',
          flexShrink: 0,
        }}
      >
        UNVERIFIED ✗
      </span>
    )
  }
  return (
    <span
      style={{
        fontSize: 10,
        padding: '2px 7px',
        borderRadius: 4,
        background: 'rgba(245,158,11,0.12)',
        color: '#f59e0b',
        fontWeight: 700,
        letterSpacing: '0.05em',
        border: '1px solid rgba(245,158,11,0.25)',
        flexShrink: 0,
      }}
    >
      PENDING
    </span>
  )
}

// ── Full card row ──────────────────────────────────────────────────────────────

function Row({ label, value, mono = true }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        fontSize: 12,
        borderBottom: '1px solid rgba(255,255,255,0.05)',
        padding: '7px 0',
        alignItems: 'flex-start',
      }}
    >
      <span
        style={{
          color: 'var(--muted)',
          flexShrink: 0,
          width: 90,
          paddingTop: 1,
          fontSize: 11,
        }}
      >
        {label}
      </span>
      <span
        style={{
          color: 'var(--text)',
          fontFamily: mono ? 'monospace' : undefined,
          wordBreak: 'break-all',
          flex: 1,
        }}
      >
        {value}
      </span>
    </div>
  )
}

// ── Compact mode ───────────────────────────────────────────────────────────────

function CompactView({ artifact, receipt }: { artifact?: ArtifactData; receipt?: ReceiptData }) {
  const agentName = receipt?.agent_name || artifact?.agent_name || '—'
  const type = artifact?.type || '—'
  const hash = trunc(artifact?.hash, 18)
  const verified = receipt?.verified

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '8px 12px',
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        fontSize: 12,
      }}
    >
      <span style={{ color: 'var(--cyan)', fontSize: 14 }}>◈</span>
      <span style={{ color: 'var(--muted)', fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        PoW
      </span>
      <span style={{ color: 'var(--text)', fontWeight: 600 }}>{agentName}</span>
      <span
        style={{
          padding: '1px 6px',
          borderRadius: 4,
          background: 'rgba(0,245,255,0.1)',
          color: 'var(--cyan)',
          fontSize: 10,
          fontFamily: 'monospace',
        }}
      >
        {type}
      </span>
      <span style={{ fontFamily: 'monospace', color: 'rgba(255,255,255,0.45)', fontSize: 11, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {hash}
      </span>
      <VerifiedBadge verified={verified} />
    </div>
  )
}

// ── Full card ──────────────────────────────────────────────────────────────────

function FullCard({ artifact, receipt }: { artifact?: ArtifactData; receipt?: ReceiptData }) {
  const agentName  = receipt?.agent_name || artifact?.agent_name || '—'
  const taskId     = fmtTaskId(artifact?.task_id)
  const type       = artifact?.type || '—'
  const capability = receipt?.capability || '—'
  const uri        = artifact?.uri ? trunc(artifact.uri, 36) : '—'
  const hash       = artifact?.hash ? trunc(artifact.hash, 36) : '—'
  const receiptId  = receipt?.receipt_id ? trunc(receipt.receipt_id, 24) : '—'
  const verified   = receipt?.verified
  const createdAt  = artifact?.created_at ? fmtDate(artifact.created_at) : '—'

  return (
    <div
      style={{
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 16px',
          borderBottom: '1px solid var(--border)',
          background: 'rgba(0,0,0,0.3)',
        }}
      >
        <span style={{ color: 'var(--cyan)', fontSize: 16, lineHeight: 1 }}>◈</span>
        <span
          style={{
            fontSize: 11,
            fontWeight: 700,
            letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: 'var(--cyan)',
          }}
        >
          Proof of Work
        </span>
        <div style={{ marginLeft: 'auto' }}>
          <VerifiedBadge verified={verified} />
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: '4px 16px 12px' }}>
        <Row label="Agent"      value={agentName}    mono={false} />
        <Row label="Task"       value={taskId} />
        <Row label="Type"       value={type} />
        <Row label="Capability" value={capability} />
        <Row label="Artifact"   value={uri} />
        <Row label="Hash"       value={hash} />
        <Row label="Receipt"    value={receiptId} />
        <Row
          label="Verified"
          value={
            verified === true
              ? '✓ verified'
              : verified === false
              ? '✗ failed'
              : '○ pending'
          }
          mono={false}
        />
        <Row label="Created"    value={createdAt} />
      </div>
    </div>
  )
}

// ── Export ─────────────────────────────────────────────────────────────────────

export default function ProofOfWork({ artifact, receipt, compact = false }: ProofOfWorkProps) {
  if (!artifact && !receipt) {
    return (
      <div
        style={{
          padding: '12px 16px',
          borderRadius: 8,
          border: '1px dashed rgba(255,255,255,0.1)',
          fontSize: 12,
          color: 'var(--muted)',
          textAlign: 'center',
        }}
      >
        No proof available
      </div>
    )
  }

  if (compact) {
    return <CompactView artifact={artifact} receipt={receipt} />
  }

  return <FullCard artifact={artifact} receipt={receipt} />
}
