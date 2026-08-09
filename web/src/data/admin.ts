/* 1층 스펙 사람 검수 도구의 백엔드 연결(ADR-0016) — **로컬 전용**.
 *
 * `api.ts` 와 나란한 자리지만 성격이 다르다: 저건 공개 화면이 읽는 창구고, 이건
 * `ADMIN_ENABLED=1` 로 띄운 로컬 API 에만 존재하는 관리 라우트다. 켜지 않은 서버에서는
 * `/api/admin/*` 가 **404** 라, 화면이 그걸 '아직 안 켰어요'로 읽는다(에러가 아니다).
 *
 * ⚠️ `embedUrl` 을 여기서 조립하지 말 것 — 백엔드 `source_links.embed_url` 이 만든 값을
 *    그대로 받는다. 틀린 링크는 링크 없음보다 나쁘다(ADR-0009).
 */
const BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

/** 사람이 고칠 수 있는 칸 — 백엔드 `spec_overrides.OVERRIDABLE` 과 **같은 순서**. */
export const FIELDS = [
  { key: 'base_combo', ko: '풀 조합', hint: '캡션에 적힌 풀 이름만 적어요' },
  { key: 'scent', ko: '향', hint: '향료 이름이에요' },
  { key: 'slime_type', ko: '종류', hint: '클리어·버터·폼볼 같은 분류예요' },
  { key: 'official_texture', ko: '질감(판매자 서술)', hint: '판매자가 쓴 질감 설명이에요' },
] as const

export type FieldKey = (typeof FIELDS)[number]['key']

export type QueueItem = {
  market: string
  marketLabel: string
  product: string
  values: Record<string, string | string[] | null>
  /** 큐를 띄운 칸 — 이 항목에서 **실제로 비어 있는** 칸만 들어온다. */
  missing: string[]
  /** '보고도 모름'으로 이미 판정된 칸. */
  unknown: string[]
  permalink: string | null
  embedUrl: string | null
  index: number
  total: number
}

export type SaveResult = {
  market: string
  product: string
  saved: string[]
  unknown: string[]
  remaining: number
  next: QueueItem | null
}

/** 관리 라우트가 꺼져 있을 때(404) 던지는 신호. 화면이 에러가 아니라 안내로 그린다. */
export class AdminDisabled extends Error {}

export async function fetchSpecQueue(): Promise<QueueItem[]> {
  const res = await fetch(`${BASE}/api/admin/spec-queue`)
  if (res.status === 404) throw new AdminDisabled()
  if (!res.ok) throw new Error(`spec-queue ${res.status}`)
  return res.json()
}

export async function saveSpecOverride(body: {
  market: string
  product: string
  fields: Record<string, string | string[] | null>
  unknown_fields: string[]
  note?: string | null
}): Promise<SaveResult> {
  const res = await fetch(`${BASE}/api/admin/spec-override`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (res.status === 404) throw new AdminDisabled()
  if (!res.ok) throw new Error(`spec-override ${res.status}: ${await res.text()}`)
  return res.json()
}
