/**
 * 1층 스펙 사람 검수 화면(ADR-0016) — `/review`, **로컬 전용**.
 *
 * 왜 있는가: 캡션 추출은 판매자가 안 쓴 것을 만들어낼 수 없다(1급 규칙). 판매자가 풀조합·향·
 * 종류·질감을 안 적으면 그 칸은 LLM 을 몇 번 더 돌려도 영구히 빈칸이다 — 사람이 게시물을
 * 보고 넣는 수밖에 없다. 이 화면이 그 한 바퀴를 돌리는 도구다.
 *
 * ⚠️ **이 화면은 픽셀 대조 계약 밖이다.** `SlimeSearch.tsx` 는 디자인 HTML 축자 이식이라
 *    인라인 style 값이 원본과 한 글자도 다르지 않고 0.025% 픽셀 차이가 근거로 걸려 있다.
 *    여기는 **디자인 원본이 없는 내부 도구**다 — 나중에 누가 목업과 diff 하지 말 것.
 *    같은 KDS 컴포넌트·토큰은 그대로 쓴다(`components/kds/` 편집 금지 규칙 준수).
 *
 * ⚠️ 임베드는 fail-closed 다. `embedUrl` 은 백엔드가 만든 값만 쓰고, 없으면 iframe 대신
 *    안내 + URL 입력칸을 띄운다 — 프런트에서 URL 을 조립하지 않는다(ADR-0009).
 *    큐 39건 중 7건(머머 전부)이 permalink 결손이라 이건 예외 처리가 아니라 설계 대상이다.
 *
 * ⚠️ 사람도 1급 규칙을 지킨다: **게시물에서 확인되는 것만** 넣고, 모르면 '모름'이다.
 *    그래서 [모름으로 표시] 는 저장 버튼과 같은 줄, 같은 크기에 둔다 — 추측 입력을 유도하는
 *    UI 는 LLM 이 지어내는 것과 같은 실패를 사람 손으로 만든다.
 */
import { useCallback, useEffect, useState } from 'react'

import Badge from '../components/kds/Badge'
import Button from '../components/kds/Button'
import Chip from '../components/kds/Chip'
import Icon from '../components/kds/Icon'
import Input from '../components/kds/Input'
import {
  AdminDisabled, FIELDS, fetchSpecQueue, saveSpecOverride,
  type QueueItem,
} from '../data/admin'

/** 큐를 띄우지는 않지만 카드에서 **편집은 되는** 칸(사용자 결정 2026-08-09). */
const EXTRA_FIELDS = [
  { key: 'beads', ko: '비즈·토핑', hint: '쉼표로 구분해요. 없으면 비워 둬요' },
  { key: 'source_permalink', ko: '출처 게시물 URL', hint: '인스타 게시물 주소예요' },
] as const

const card: React.CSSProperties = {
  background: 'var(--kds-bg)',
  border: '1px solid var(--kds-border)',
  borderRadius: 'var(--kds-radius-16)',
  padding: 24,
}

const label: React.CSSProperties = {
  fontSize: 'var(--kds-text-m-size)',
  color: 'var(--kds-fg-tertiary)',
}

/** 배열 칸 ↔ 입력칸 문자열. 저장 직전에만 배열로 되돌린다. */
const toText = (v: string | string[] | null): string =>
  Array.isArray(v) ? v.join(', ') : (v ?? '')

/** 이 항목의 입력칸 초기값 — 이미 값이 있는 칸은 **prefill 해서 고칠 수 있게** 한다.
 *  NULL 채우기만 되는 도구는 반쪽이다: 추출기가 향료·재료어를 제품명으로 잡은 사례가
 *  이 레포에 실측돼 있다(유령 제품 복구). */
function draftOf(item: QueueItem): Record<string, string> {
  const d: Record<string, string> = {}
  for (const f of [...FIELDS, ...EXTRA_FIELDS]) d[f.key] = toText(item.values[f.key])
  return d
}

export default function SpecReview() {
  const [queue, setQueue] = useState<QueueItem[] | null>(null)
  const [cursor, setCursor] = useState(0)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [disabled, setDisabled] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    fetchSpecQueue()
      .then((q) => {
        setQueue(q)
        setDraft(q.length ? draftOf(q[0]) : {})
      })
      .catch((e) => (e instanceof AdminDisabled ? setDisabled(true) : setError(String(e))))
  }, [])

  const item = queue && cursor < queue.length ? queue[cursor] : null

  /** 다음 항목으로. 큐는 **저장 시점에 다시 받지 않는다** — 서버가 준 순서를 그대로 도는 게
   *  39건을 한 바퀴 도는 방식이고, 순서가 중간에 바뀌면 같은 항목을 다시 만나거나 건너뛴다.
   *  남은 건수만 서버 응답으로 갱신한다. */
  const advance = useCallback(() => {
    setCursor((c) => {
      const next = c + 1
      if (queue && next < queue.length) setDraft(draftOf(queue[next]))
      return next
    })
  }, [queue])

  const submit = useCallback(
    async (mode: 'save' | 'unknown') => {
      if (!item || busy) return
      setBusy(true)
      setError(null)
      try {
        const fields: Record<string, string | string[] | null> = {}
        if (mode === 'save') {
          for (const f of [...FIELDS, ...EXTRA_FIELDS]) {
            const typed = (draft[f.key] ?? '').trim()
            if (typed === toText(item.values[f.key]).trim()) continue // 안 건드린 칸은 안 보낸다
            fields[f.key] = f.key === 'beads'
              ? typed.split(',').map((s) => s.trim()).filter(Boolean)
              : typed
          }
        }
        // '모름'은 **비어 있는 칸에만** 찍는다 — 값이 있는 칸을 모름으로 덮으면 그건 삭제다.
        const unknown = mode === 'unknown' ? item.missing : []
        if (!Object.keys(fields).length && !unknown.length) {
          setStatus('바뀐 게 없어서 그냥 넘어가요')
          advance()
          return
        }
        const res = await saveSpecOverride({
          market: item.market, product: item.product, fields, unknown_fields: unknown,
        })
        setStatus(
          mode === 'unknown'
            ? `모름으로 표시했어요 · ${res.remaining}건 남았어요`
            : `저장했어요 (${res.saved.join(', ') || '변경 없음'}) · ${res.remaining}건 남았어요`,
        )
        advance()
      } catch (e) {
        setError(e instanceof AdminDisabled ? '관리 라우트가 꺼져 있어요' : String(e))
      } finally {
        setBusy(false)
      }
    },
    [item, draft, busy, advance],
  )

  // 39건을 타이핑하는 도구라 이게 실질 UX 다 — 마우스로 버튼을 찾아가면 흐름이 끊긴다.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        void submit('save')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [submit])

  const shell = (children: React.ReactNode) => (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '40px 20px 80px' }}>{children}</div>
  )

  if (disabled) {
    return shell(
      <div style={card}>
        <h1 style={{ fontSize: 'var(--kds-title-s-size)', margin: '0 0 12px' }}>
          검수 도구가 꺼져 있어요
        </h1>
        <p style={{ color: 'var(--kds-fg-secondary)', lineHeight: 1.7, margin: 0 }}>
          이 화면은 로컬 전용이에요. API 를 <code>ADMIN_ENABLED=1</code> 로 띄우면 열려요.
          <br />
          <code>ADMIN_ENABLED=1 uvicorn api.main:app --reload --port 8000</code>
        </p>
      </div>,
    )
  }

  if (error && !queue) return shell(<div style={card}>불러오지 못했어요 — {error}</div>)
  if (!queue) return shell(<div style={card}>큐를 불러오는 중이에요…</div>)

  if (!item) {
    return shell(
      <div style={{ ...card, textAlign: 'center' }}>
        <Icon name="check" size={32} color="var(--kds-positive)" />
        <h1 style={{ fontSize: 'var(--kds-title-s-size)', margin: '12px 0 8px' }}>
          {queue.length ? '한 바퀴 다 돌았어요' : '검수할 항목이 없어요'}
        </h1>
        <p style={{ color: 'var(--kds-fg-secondary)', margin: 0 }}>
          {status ?? '새로고침하면 남은 항목을 다시 불러와요.'}
        </p>
      </div>,
    )
  }

  const remaining = queue.length - cursor

  return shell(
    <>
      {/* 진행도 — 39건짜리 유한 큐라 '몇 개 남았는지'가 계속 보여야 한다. */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    marginBottom: 16 }}>
        <span style={{ fontSize: 'var(--kds-text-xl-size)',
                       fontWeight: 'var(--kds-weight-bold)' }}>
          1층 스펙 검수
        </span>
        <span style={label}>
          {cursor + 1} / {queue.length} · {remaining}건 남았어요
        </span>
      </div>

      <div style={card}>
        {/* 마켓 · 제품 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
          <Badge>{item.marketLabel}</Badge>
          {item.unknown.length > 0 && <Chip>모름 {item.unknown.length}칸</Chip>}
        </div>
        <h1 style={{ fontSize: 'var(--kds-title-s-size)', margin: '4px 0 16px' }}>
          {item.product}
        </h1>

        {/* 판매자 본인 게시물만 임베드한다(ADR-0009). 바이트는 인스타가 서빙 — 우리가 받아서
            다시 뿌리지 않는다. 식별자가 없으면 이 분기 자체가 죽는다(fail-closed). */}
        {item.embedUrl ? (
          <div style={{ position: 'relative', width: '100%', height: 520, marginBottom: 8 }}>
            <iframe
              src={item.embedUrl}
              title={`${item.marketLabel} ${item.product} 판매자 게시물`}
              style={{ width: '100%', height: '100%', border: '1px solid var(--kds-border)',
                       borderRadius: 'var(--kds-radius-12)', background: 'var(--kds-white)' }}
              loading="lazy"
            />
          </div>
        ) : (
          <div style={{ padding: 20, marginBottom: 8, textAlign: 'center',
                        borderRadius: 'var(--kds-radius-12)',
                        background: 'var(--kds-bg-subtle)', color: 'var(--kds-fg-secondary)' }}>
            출처 게시물 URL 이 없어서 미리보기를 못 띄워요. 아래에 주소를 넣으면 보여요.
          </div>
        )}
        {/* 임베드는 보통 첫 프레임 + 재생 버튼이라 영상이 인라인으로 안 돌 수 있다 —
            원문 링크를 **항상** 같이 둔다. 클릭 한 번으로 인스타에서 본다. */}
        {item.permalink && (
          <p style={{ margin: '0 0 20px' }}>
            <a href={item.permalink} target="_blank" rel="noreferrer"
               style={{ fontSize: 'var(--kds-text-m-size)', color: 'var(--kds-accent)' }}>
              원문 보기 ↗
            </a>
          </p>
        )}

        {/* 사람도 1급 규칙을 지킨다. 이 문장이 [모름으로 표시] 버튼의 근거다. */}
        <p style={{ ...label, margin: '0 0 16px', lineHeight: 1.6 }}>
          게시물에서 확인되는 것만 적어요. 모르면 모름으로 표시해요.
        </p>

        <div style={{ display: 'grid', gap: 16 }}>
          {[...FIELDS, ...EXTRA_FIELDS].map((f) => {
            const blank = item.missing.includes(f.key)
            const unknownHere = item.unknown.includes(f.key)
            return (
              <Input
                key={f.key}
                field={f.key === 'official_texture' ? 'textarea' : 'text'}
                rows={3}
                label={
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    {f.ko}
                    {blank && <Chip>빈 칸</Chip>}
                    {unknownHere && <Chip>모름</Chip>}
                  </span>
                }
                placeholder={f.hint}
                value={draft[f.key] ?? ''}
                onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
              />
            )
          })}
        </div>

        {/* [모름으로 표시] 는 저장과 **같은 줄·같은 크기**다 — 추측 입력을 유도하지 않으려고. */}
        <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
          <Button hierarchy="secondary" size="l" fullWidth disabled={busy}
                  onClick={() => void submit('unknown')}>
            모름으로 표시
          </Button>
          <Button hierarchy="primary" size="l" fullWidth disabled={busy}
                  onClick={() => void submit('save')}>
            저장하고 다음 (⌘/Ctrl+Enter)
          </Button>
        </div>
      </div>

      {(status || error) && (
        <p style={{ marginTop: 16, fontSize: 'var(--kds-text-m-size)',
                    color: error ? 'var(--kds-negative)' : 'var(--kds-fg-secondary)' }}>
          {error ?? status}
        </p>
      )}
    </>,
  )
}
