/* 백엔드 연결 — `mock.ts` 와 **같은 shape** 을 돌려준다.
 *
 * 그래서 화면(`screens/SlimeSearch.tsx`)의 마크업을 한 줄도 안 고치고 데이터만 갈아끼울 수
 * 있다. 디자인 원본과 픽셀 대조가 가능한 상태를 유지하는 게 이 파일의 존재 이유다.
 *
 * ⚠️ 본문(`body`)은 서버가 이미 자른 발췌다(ADR-0013 §3). 여기서 다시 이어붙이거나
 *    전문을 따로 요청하지 말 것 — 자르는 자리는 백엔드 한 곳이어야 한다.
 */
import { MARKET_SCOPE_NOTE, PLACEHOLDER_SCORE } from './mock'
import type { Cell, Criterion, CriterionRow, Scope, SummaryRow, SummaryScore } from './mock'

const BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

type RawReview = {
  platform: string
  market: string | null
  product: string | null
  evidence: string | null
  sentiment: string | null
  url: string | null
  is_comment: boolean
  collected_at: string | null
  body: string | null
  title: string | null
  author: string | null
  posted_at: string | null
  likes: number | null
  views: number | null
  comment_count: number | null
  votes: number | null
}

type RawPage = {
  market: string | null
  marketLabel: string | null
  marketLogo: { kind: string; src?: string; url?: string; text?: string } | null
  product: string | null
  spec: {
    official_scent: string | null
    base_combo: string | null
    slime_type: string | null
    official_texture: string | null
    beads: string[]
    source_permalink: string | null
  } | null
  embedUrl: string | null
  summary: { integrated: SummaryRow[]; instagram: SummaryRow[]; dcinside: SummaryRow[] }
  byCriterion: Record<string, CriterionRow>
  summaryMeta: { generatedAt: string | null; model: string | null; nReviews: number | null }
  /* 주문 축(고객 응대·배송)은 마켓당 한 벌이라 근거 모집단이 다르다 — 제품 후기 수가 아니라
   * 접은 주문 조각 수다(ADR-0015). 두 값을 같은 '건수'로 섞어 쓰지 말 것. */
  marketSummaryMeta: { generatedAt: string | null; model: string | null; nOrders: number | null }
  nReviews: number
  igReviews: RawReview[]
  dcReviews: RawReview[]
  igCount: number
  dcCount: number
}

/** 값이 없을 때 디자인의 자리표시자를 그대로 쓴다 — 레이아웃이 무너지지 않게. */
const DASH = '—'

/** 아직 요약이 없는 기준의 빈 행. 매 렌더 새 객체를 만들면 표가 통째로 리렌더된다. */
const EMPTY_CELL: Cell = { verdict: null, minority: null }
/* 축마다 하나씩 미리 만들어 둔다 — `{...EMPTY_ROW, scope}` 로 즉석 생성하면 매 렌더 새 객체가
 * 되어 표가 통째로 리렌더된다(위 주석과 같은 이유). */
const EMPTY_ROWS: Record<Scope, CriterionRow> = {
  product: { ig: EMPTY_CELL, dc: EMPTY_CELL, all: EMPTY_CELL, scope: 'product' },
  market: { ig: EMPTY_CELL, dc: EMPTY_CELL, all: EMPTY_CELL, scope: 'market' },
}

/* 정렬 키 — 화면(`SlimeSearch`)의 좋아요순/조회순/추천순이 쓰는 값. 표시용 문자열(`—`,
 * `12건`)로는 정렬이 안 되므로 원본 숫자를 따로 실어 보낸다.
 * ⚠️ 없는 값은 0 이 아니라 **모름**이다(인스타에 조회/추천이 없고, 디시 댓글엔 글단위 지표가
 *    없다). 0 으로 접으면 '지표 없음'이 '0 회'로 둔갑해 꼴찌 자리를 실제 0 과 나눠 갖는다 —
 *    -Infinity 로 꼬리에 몰아 둔다. 인스타는 좋아요를 숨긴 게시물에 **-1** 을 주므로(Apify)
 *    음수도 같은 '모름'으로 본다. */
export type SortKey = { date: string; likes: number; views: number; votes: number }

const UNKNOWN = Number.NEGATIVE_INFINITY
const num = (v: number | null | undefined): number => (v == null || v < 0 ? UNKNOWN : v)

function sortKey(r: RawReview): SortKey {
  const d = fmtDate(r)
  return {
    // 날짜는 백엔드가 이미 'YYYY.MM.DD' 로 준다 — 사전순 = 시간순이라 파싱이 필요 없다.
    // 카드에 그리는 날짜와 **같은 값**을 쓴다: 보이는 순서와 정렬 기준이 어긋나지 않게.
    date: d === DASH ? '' : d,
    likes: num(r.likes),
    views: num(r.views),
    votes: num(r.votes),
  }
}

function fmtDate(r: RawReview): string {
  // 작성일이 있으면 작성일, 없으면 수집일. 디시 옛 글은 목록 표기에 연도가 없어 버려지는 경우가
  // 있는데(index._ts), 그때 '수집일'을 쓴다 — 틀린 날짜보다 낫다.
  return r.posted_at ?? r.collected_at ?? DASH
}

export type PageData = {
  market: string
  marketLogo: string | null
  marketUrl: string | null
  marketMonogram: string | null
  product: string
  media: { caption: string; spec: string; embedUrl: string | null }
  spec: { glues: string[]; scent: string; slimeType: string; texture: string }
  summary: {
    all: SummaryRow[]; allBasis: string
    ig: SummaryRow[]; igBasis: string
    dc: SummaryRow[]; dcBasis: string
    /* 마켓 축 줄(고객 응대·배송)에 붙는 설명. `allBasis` 는 제품 후기 수라 이 두 줄에는
     * 해당하지 않는다 — 근거가 다르면 그 자리에서 말해야 한다(ADR-0015). */
    marketNote: string
    /* ⚠️ 지금은 **자리표시자**다 — 백엔드에 점수 산출이 없다(`mock.PLACEHOLDER_SCORE` 주석). */
    score: SummaryScore
  }
  byCriterion: (c: Criterion) => CriterionRow
  igReviews: {
    id: string; account: string; date: string; body: string; url: string
    sortKey: SortKey
  }[]
  igCount: string
  dcReviews: {
    id: string; title: string; meta: string; body: string
    comments: string; votes: string; url: string
    sortKey: SortKey
  }[]
  dcCount: string
}

function toPageData(d: RawPage): PageData {
  const logo = d.marketLogo
  return {
    market: d.marketLabel ?? d.market ?? '마켓 미상',
    marketLogo: logo?.kind === 'image' && logo.src ? `${BASE}${logo.src}` : null,
    marketUrl: logo?.url ?? null,
    marketMonogram: logo?.kind === 'monogram' ? (logo.text ?? null) : null,
    product: d.product ?? '제품 미선택',

    media: {
      caption: d.embedUrl ? '판매자 공식 게시물' : '공식 미디어 없음',
      spec: d.embedUrl ? '인스타그램 임베드' : '판매자 게시물 링크가 등록되면 보여요',
      embedUrl: d.embedUrl,
    },

    spec: {
      // 풀 조합은 디자인이 칩 여러 개로 그린다 — 공백/슬래시 어느 쪽으로 적혀도 나뉘게.
      glues: (d.spec?.base_combo ?? '').split(/\s*[/,]\s*|\s+/).filter(Boolean),
      scent: d.spec?.official_scent ?? DASH,
      // 카드는 '종류'를 보여주고 `beads` 는 안 보여준다(사용자 결정 2026-08-06). 내장 알갱이형
      // 제품에선 둘이 같은 단어로 겹치는데(빠코볼: 종류 '폼볼' / 토핑 '폼볼'), 구매자가 먼저
      // 읽는 건 분류 쪽이다. `beads` 는 DB·API 에 그대로 살아 있다 — 지운 건 표시뿐.
      slimeType: d.spec?.slime_type ?? DASH,
      // '질감' 줄에는 **판매자 서술만** 넣는다. `slime_type` 은 종류 분류지 질감이 아니고,
      // 이제 바로 위 '종류' 줄이 그 값을 그대로 갖는다.
      // ⚠️ 없을 때 slime_type 으로 폴백하지 말 것 — 그게 질감 줄에 분류코드만 뜨던 원래 버그다.
      texture: d.spec?.official_texture ?? DASH,
    },

    /* 요약은 **축별 줄 목록**이다(ADR-0014) — 문단 한 덩어리가 아니다. 백엔드가 verdict 가
     * 빈 기준을 이미 빼고 보내므로 여기서 다시 거르지 않는다(거르는 자리는 한 곳이어야 한다). */
    summary: {
      all: d.summary.integrated ?? [],
      allBasis: `인스타 ${d.igCount}건 + 아모스갤 ${d.dcCount}건`,
      ig: d.summary.instagram ?? [],
      igBasis: `출처 ${d.igCount}건`,
      dc: d.summary.dcinside ?? [],
      dcBasis: `출처 ${d.dcCount}건`,
      /* 건수를 붙이지 않는다(사용자 결정 2026-08-07) — `marketSummaryMeta.nOrders` 는
       * provenance 로만 온다. 이유는 ADR-0014 개정과 같다: 숫자는 화면에서 걷어냈다. */
      marketNote: MARKET_SCOPE_NOTE,
      /* 점수는 백엔드가 **아직 안 준다** — 산출 로직이 없다(사용자 결정 2026-08-07: 곧 도입).
       * 그때까지 디자인 자리표시자를 그대로 쓴다. 응답에 `score` 가 실리기 시작하면
       * `d.score ?? PLACEHOLDER_SCORE` 가 아니라 **폴백을 지우고** 응답만 쓴다 — 폴백이
       * 남아 있으면 생성 실패한 페이지가 조용히 가짜 숫자를 띄운다. */
      score: PLACEHOLDER_SCORE,
    },

    /* 6기준 표 — 키는 백엔드의 `consolidated_view.CRITERIA` 가 정한다(ADR-0011: 한 리스트가
     * 스키마·프롬프트·화면표를 동시에 움직인다). 여기서 키를 새로 만들면 그 계약이 깨진다.
     * 아직 생성 전이면 전부 null → 화면이 '언급 없음'으로 degrade. */
    byCriterion: (c: Criterion) => d.byCriterion?.[c.key] ?? EMPTY_ROWS[c.scope],

    igReviews: d.igReviews.map((r, i) => ({
      id: r.url ?? `ig-${i}`,
      account: r.author ? `@${r.author}` : '계정 미상',
      date: fmtDate(r),
      body: r.body ?? r.evidence ?? '',
      url: r.url ?? '',
      sortKey: sortKey(r),
    })),
    igCount: `${d.igCount}건`,

    dcReviews: d.dcReviews.map((r, i) => ({
      id: r.url ? `${r.url}#${i}` : `dc-${i}`,
      title: r.title ?? '(제목 없음)',
      meta: `${r.author ?? 'ㅇㅇ'} · ${fmtDate(r)}`,
      body: r.body ?? r.evidence ?? '',
      comments: r.comment_count == null ? DASH : String(r.comment_count),
      votes: r.votes == null ? DASH : String(r.votes),
      url: r.url ?? '',
      sortKey: sortKey(r),
    })),
    dcCount: `${d.dcCount}건`,
  }
}

export async function fetchMarkets(): Promise<string[]> {
  const res = await fetch(`${BASE}/api/markets`)
  if (!res.ok) throw new Error(`markets ${res.status}`)
  return res.json()
}

export async function fetchProducts(market: string): Promise<{ product: string }[]> {
  const res = await fetch(`${BASE}/api/markets/${encodeURIComponent(market)}/products`)
  if (!res.ok) throw new Error(`products ${res.status}`)
  return res.json()
}

export async function fetchPage(market: string, product: string): Promise<PageData> {
  const qs = new URLSearchParams()
  if (market) qs.set('market', market)
  if (product) qs.set('product', product)
  const res = await fetch(`${BASE}/api/page?${qs}`)
  if (!res.ok) throw new Error(`page ${res.status}`)
  return toPageData(await res.json())
}
