/**
 * Slime Search — `Slime Search.dc.html` 를 **그대로 옮긴** 화면.
 *
 * 원칙: 인라인 style 값은 디자인 원본과 한 글자도 다르지 않다. 옮기면서 바꾼 것은
 * JSX 문법상 불가피한 것뿐이다:
 *   · `style="a:b"`                → `style={{ a: 'b' }}`
 *   · `<x-import …Input …>`        → `<Input …>` (`hint-size` 는 디자인 도구 전용 속성이라 버림)
 *   · `<sc-for list as="x">`       → `.map()`
 *   · `<sc-if value="…">`          → `{cond && …}`
 *   · `style-hover="…"`            → 인라인으로 불가능 → `globals.css` 의 `.sortMenuItem:hover`
 *   · `DCLogic.state` / `renderVals()` → `useState` (아래 주석에 1:1 대응 표시)
 *
 * 데이터는 `../data/api`(FastAPI) 에서 오고, 오기 전/실패 시엔 `../data/mock` 의 자리표시자를
 * 그대로 쓴다 — 레이아웃이 흔들리지 않고 원본 목업과 픽셀 대조가 계속 가능하다.
 *
 * ⚠️ 리뷰 본문은 **서버가 자른 발췌**다(ADR-0013 §3). 여기서 펼치지 말 것 — 전문으로 가는
 *    길은 카드의 '원문 보기' 하나뿐이다.
 * ⚠️ 목록 아래는 디자인의 '더보기' 버튼이 아니라 **쪽 번호**다(사용자 결정 2026-08-06).
 *    누적식 '더보기'는 정렬을 바꾸면 이미 펼친 만큼을 어떻게 할지가 애매해지고, 목록이
 *    길어질수록 되돌아갈 방법이 없다.
 * ⚠️ 값이 없는 자리는 `—` 로 나온다: 인스타에는 조회/추천이, 디시 댓글에는 글단위 지표가 없다.
 */
import { useEffect, useState } from 'react'

import Badge from '../components/kds/Badge'
import Button from '../components/kds/Button'
import Chip from '../components/kds/Chip'
import Icon from '../components/kds/Icon'
import Input from '../components/kds/Input'
import Tab from '../components/kds/Tab'
import { CRITERIA, page as PLACEHOLDER } from '../data/mock'
import type { Cell, SummaryRow, SummaryScore } from '../data/mock'
import { fetchPage, type PageData, type SortKey } from '../data/api'

type Src = 'both' | 'ig' | 'dc'
type Panel = 'ig' | 'dc'

/* 정렬 메뉴의 `key` 는 곧 `sortKey` 의 필드명이다 — 라벨과 실제 기준이 갈리지 않게 한 자리에서
 * 묶는다('최신순'만 필드명이 `date` 라 따로 적는다). */
const IG_SORTS = [
  { key: 'date', label: '최신순' },
  { key: 'likes', label: '좋아요순' },
] as const
const DC_SORTS = [
  { key: 'date', label: '최신순' },
  { key: 'views', label: '조회순' },
  { key: 'votes', label: '추천순' },
] as const

/** 선택한 기준으로 내림차순 정렬한 사본. 값이 '모름'인 항목은 꼬리로 간다(-Infinity / '').
 *  `sort` 는 안정 정렬이라 동점은 서버가 준 순서(최근 수집순)를 그대로 유지한다. */
function sortReviews<T extends { sortKey: SortKey }>(rows: T[], key: keyof SortKey): T[] {
  return [...rows].sort((a, b) => {
    const x = a.sortKey[key]
    const y = b.sortKey[key]
    return x < y ? 1 : x > y ? -1 : 0
  })
}

/* 후기 목록은 '더보기' 누적이 아니라 **쪽 번호**로 넘긴다(사용자 결정 2026-08-06).
 * 자르는 건 화면 쪽이다 — `/api/page` 가 목록을 통째로 주고, 정렬도 여기서 하기 때문에
 * 서버 페이징을 섞으면 정렬 기준이 페이지마다 갈린다. */
const PAGE_SIZE = 5

/** 한 번에 보여줄 쪽 번호는 최대 5개. 현재 쪽을 가운데 두되 양끝에서는 밀린다. */
function pageWindow(cur: number, total: number): number[] {
  const span = Math.min(5, total)
  const start = Math.max(1, Math.min(cur - 2, total - span + 1))
  return Array.from({ length: span }, (_, i) => start + i)
}

const pagerBtn = {
  minWidth: 36,
  height: 36,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'none',
  border: 0,
  borderRadius: 'var(--kds-radius-8)',
  padding: '0 6px',
  fontFamily: 'inherit',
  fontSize: 'var(--kds-text-l-size)',
} as const

/* 줄글 두 곳 — 1층 스펙의 '질감'(판매자 촉감 설명)과 '통합 요약' — 만 KDS `text-xl` 의
 * 줄간격 24px 대신 28px 을 쓴다(사용자 지시 2026-08-06). 한 낱말짜리 값이 아니라 여러 줄
 * 문장이라 24px 로는 빽빽하게 읽힌다. 글자 크기는 건드리지 않는다 — 줄간격만. */
const PROSE_LINE = '28px'

/* 요약 카드의 '통합 요약' 제목 줄에 붙던 반짝임 아이콘(`AiSparkle`, 사용자 지시 2026-08-06)은
 * 그 제목 줄과 함께 **빠졌다**(2026-08-07 디자인 개편). 디자인의 요약 카드는 링 게이지로
 * 시작하고 카드 안에 제목이 없다 — 아이콘이 붙을 자리가 사라진 것이지, 철회한 게 아니다.
 * 되살리려면 커밋 히스토리에서 이 자리의 SVG(면으로 채운 네갈래 별 2개)를 그대로 꺼내면 된다. */

/* 펼치기/접기 버튼 — 아이콘을 라벨 **왼쪽**에 둔다(사용자 지시 2026-08-06).
 * 아이콘은 '지금 상태'가 아니라 **누르면 갈 방향**을 가리킨다: 접힘 → ›, 펼침 → ⌄.
 * 아이콘도 라벨도 회색이다(사용자 지시 2026-08-06) — 액센트는 후기 카드의 '원문 보기'가
 * 가져간다. 화면의 초록은 진짜 나가는 링크에만 남긴다. */
function Disclosure({
  open,
  onClick,
  labels,
  controls,
  style,
}: {
  open: boolean
  onClick: () => void
  labels: [closed: string, opened: string]
  controls?: string
  style?: React.CSSProperties
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={open}
      aria-controls={controls}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        background: 'none',
        border: 0,
        padding: 0,
        cursor: 'pointer',
        fontFamily: 'inherit',
        fontSize: 'var(--kds-text-l-size)',
        fontWeight: 'var(--kds-weight-medium)',
        color: 'var(--kds-fg-secondary)',
        ...style,
      }}
    >
      <Icon
        name={open ? 'chevron-down' : 'chevron-right'}
        size={18}
        color="var(--kds-fg-secondary)"
      />
      {open ? labels[1] : labels[0]}
    </button>
  )
}

function Pager({
  cur,
  total,
  onPick,
  label,
}: {
  cur: number
  total: number
  onPick: (p: number) => void
  label: string
}) {
  // 한 쪽뿐이면 넘길 것이 없다 — 자리도 만들지 않는다.
  if (total <= 1) return null
  const arrow = (dir: -1 | 1) => {
    const off = dir === -1 ? cur <= 1 : cur >= total
    return (
      <button
        type="button"
        onClick={() => onPick(cur + dir)}
        disabled={off}
        aria-label={dir === -1 ? '이전 페이지' : '다음 페이지'}
        style={{ ...pagerBtn, cursor: off ? 'default' : 'pointer' }}
      >
        <Icon
          name={dir === -1 ? 'chevron-left' : 'chevron-right'}
          size={18}
          color={off ? 'var(--kds-fg-disabled)' : 'var(--kds-fg-secondary)'}
        />
      </button>
    )
  }
  return (
    <nav
      aria-label={label}
      style={{
        padding: '16px 20px',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 4,
      }}
    >
      {arrow(-1)}
      {pageWindow(cur, total).map((n) => (
        <button
          key={n}
          type="button"
          onClick={() => onPick(n)}
          aria-current={n === cur ? 'page' : undefined}
          style={{
            ...pagerBtn,
            cursor: 'pointer',
            background: n === cur ? 'var(--kds-bg-subtle)' : 'none',
            fontWeight: n === cur ? 'var(--kds-weight-bold)' : 'var(--kds-weight-regular)',
            color: n === cur ? 'var(--kds-fg)' : 'var(--kds-fg-tertiary)',
          }}
        >
          {n}
        </button>
      ))}
      {arrow(1)}
    </nav>
  )
}

/* `.dc.html` 의 `<style>` 이 출처 탭 선택 상태를 민트로 덮었다. KDS 원본 Tab 은 선택 배경이
 * gray-900 이라 우리 액센트 토큰(`--kds-fg-on-accent` = 짙은 초록)과 겹쳐 글자가 안 읽힌다.
 * 디자인이 쓴 처방을 그대로 옮긴다 — 컴포넌트가 아니라 사용처에서 덮는다. */
/* 요약은 **미리 생성해 저장**한 것만 쓴다(사용자 결정 2026-08-06) — 페이지 로드마다 LLM 을
 * 부르면 열 때마다 과금된다. 아직 없으면 이 문구가 나간다. */
const NO_SUMMARY = '아직 생성하지 않았어요 — 요약은 미리 만들어 저장한 것만 보여줘요.'

/* ─────────────────────────────────────────────────────── 요약 카드 (ADR-0014)
 * ⚠️ 기준 줄에 배지·라벨을 붙이지 말 것(사용자 결정 2026-08-07). 건수('인스타 27 · 아모스갤 6')·
 *    정서 분포('갈림 19:5')·'인스타만' 을 붙여 봤고 전부 걷어냈다 — 다수/소수는 이미
 *    verdict/minority 두 칸이 문장으로 말하고 있어서 같은 말이 줄마다 두 번 붙었다.
 *    집계(`criterion_stats`)는 백엔드에 살아 있고 요약의 다수 판정 재료로 계속 쓰인다.
 */

/* 축별 아이콘 — **표시 규칙**이라 데이터가 아니라 화면이 갖는다(백엔드는 축이 무엇인지만 안다).
 * 디자인이 지정한 넷(`scoreList`)은 이름 그대로 쓰고, 마켓 축 둘은 디자인에 없어서 같은
 * Lucide 세트에서 골랐다. 없는 키는 아이콘 없이 빈 원으로 degrade 한다 — 기준이 늘어도
 * 레이아웃이 무너지지 않게. */
const SCORE_ICON: Record<string, string> = {
  texture: 'hand',
  scent: 'nose',
  sound: 'ear',
  longevity: 'hourglass',
  cs: 'headset',
  shipping: 'truck',
}

/* '향' 축 아이콘 — Lucide 에 코가 없어서 디자인이 인라인 SVG 로 직접 그렸다
 * (`Slime Search.dc.html` 150–153줄). 그 path 를 그대로 옮긴다.
 * `strokeWidth` 2 는 디자인 값이다 — KDS `Icon` 기본(1.6)과 다르지만 여기선 원본을 따른다. */
function NoseIcon({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--kds-accent)"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M13.6 2.6c.7 2.4-.3 4.6-2.1 7.3-1.5 2.2-3.7 3.7-3.7 5.8 0 2 1.6 3.4 2.8 4.2.6.4.8 1 .8 1.9" />
      <path d="M9.1 16.7c1.7-.8 3.8-.6 4.8.8 1 1.3 2.3 1.5 3.2.5.9-1 1-2.6 0-3.8" />
    </svg>
  )
}

/** 링 게이지의 stroke-dashoffset. 디자인의 `364.4 * (1 - 0.78)` 과 같은 식 —
 *  364.4 는 r=58 원둘레(2πr)고, 점수는 10점 만점이다. 숫자가 아니면 링을 비워 둔다. */
function ringOffset(score: string): number {
  const n = Number(score)
  const ratio = Number.isFinite(n) ? Math.min(Math.max(n / 10, 0), 1) : 0
  return 364.4 * (1 - ratio)
}

/* 요약 카드 본문 — 문단 하나가 아니라 **축마다 한 칸**이다(ADR-0014).
 * 질감·향·소리·지속력이 한 문단에 붙어 있으면 어느 문장이 어느 축인지 매번 되짚어야 한다.
 * 축마다 아이콘·라벨을 세우면 그 일이 사라진다. 다수 의견(verdict)과 소수 반론(minority)도
 * 한 문장에 섞지 않고 줄을 나눠 — 어느 쪽이 다수인지가 위치로 드러난다.
 *
 * ⚠️ 왼쪽 링과 축별 숫자는 **자리표시자**다(`mock.PLACEHOLDER_SCORE`). 백엔드에 점수 산출이
 *    아직 없다 — 문장(verdict/minority)만 실데이터다.
 * ⚠️ 디자인의 `scoreList` 는 제품 축 넷만 그리지만, 여기서는 백엔드가 준 줄을 **전부** 그린다.
 *    고객 응대·배송을 빼면 마켓 축 요약이 화면에서 표로만 남는데, 그 두 줄은 카드에서
 *    빠질 근거가 아니라 근거 범위를 밝혀야 할 줄이다(ADR-0015 · 바로 아래 marketNote). */
function SummaryScores({
  rows,
  marketNote,
  score,
}: {
  rows: SummaryRow[]
  marketNote: string
  score: SummaryScore
}) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '22px 32px' }}>
      {rows.map((r) => {
        const icon = SCORE_ICON[r.key]
        return (
          <div key={r.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
            <span
              style={{
                width: 44,
                height: 44,
                flex: 'none',
                borderRadius: 'var(--kds-radius-round)',
                background: 'var(--kds-accent-subtle)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              {icon === 'nose' ? (
                <NoseIcon />
              ) : (
                icon && <Icon name={icon} size={22} color="var(--kds-accent)" />
              )}
            </span>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                <span
                  style={{
                    fontSize: 'var(--kds-title-s-size)',
                    lineHeight: 1.1,
                    fontWeight: 'var(--kds-weight-bold)',
                    letterSpacing: 'var(--kds-tracking)',
                  }}
                >
                  {/* 점수가 없는 축은 숫자를 지어내지 않는다 — 화면의 '모름'은 `—` 다. */}
                  {score.byKey[r.key] ?? '—'}
                </span>
              </span>
              <span
                style={{
                  fontSize: 'var(--kds-text-m-size)',
                  lineHeight: 'var(--kds-text-m-line)',
                  color: 'var(--kds-fg-secondary)',
                }}
              >
                {r.ko}
              </span>
              {/* 마켓 축 줄(고객 응대·배송)만 근거 범위를 밝힌다(ADR-0015). 링 옆 '기반' 줄은
                * 제품 후기 수라 이 두 줄에는 해당하지 않는다 — 안 쓰면 읽는 사람이 배송 평가를
                * 이 제품 것으로 오해한다. 배지 철회(ADR-0014)의 예외가 아니다: 건수가 아니라
                * 무엇에 대한 평가인지다. */}
              {r.scope === 'market' && (
                <span
                  style={{
                    fontSize: 'var(--kds-text-m-size)',
                    lineHeight: 'var(--kds-text-m-line)',
                    color: 'var(--kds-fg-tertiary)',
                  }}
                >
                  {marketNote}
                </span>
              )}
              <p
                style={{
                  margin: '2px 0 0',
                  fontSize: 'var(--kds-text-l-size)',
                  lineHeight: 'var(--kds-text-l-line)',
                  color: 'var(--kds-fg)',
                  textWrap: 'pretty',
                }}
              >
                {r.verdict}
              </p>
              {/* 소수 반론은 다수 의견 **아래 별도 줄**이다. 한 문단에 이어 붙이면 ADR-0014 가
                * 구조로 갈라 둔 두 칸이 문장 안에서 다시 섞인다. */}
              {r.minority && (
                <p
                  style={{
                    margin: '2px 0 0',
                    fontSize: 'var(--kds-text-m-size)',
                    lineHeight: 'var(--kds-text-m-line)',
                    color: 'var(--kds-fg-tertiary)',
                    textWrap: 'pretty',
                  }}
                >
                  {r.minority}
                </p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* 표 한 칸 — 다수/소수 두 줄. 둘 다 없으면 '언급 없음'(빈칸의 뜻을 화면이 말한다). */
function CriterionCell({ cell, strong }: { cell: Cell; strong?: boolean }) {
  if (!cell.verdict) {
    return (
      <p style={{ margin: 0, fontSize: 'var(--kds-text-l-size)', lineHeight: 'var(--kds-text-l-line)', color: 'var(--kds-fg-tertiary)' }}>
        언급 없음
      </p>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <p
        style={{
          margin: 0,
          fontSize: 'var(--kds-text-l-size)',
          lineHeight: 'var(--kds-text-l-line)',
          color: strong ? 'var(--kds-fg)' : 'var(--kds-fg-secondary)',
          textWrap: 'pretty',
        }}
      >
        {cell.verdict}
      </p>
      {cell.minority && (
        <p
          style={{
            margin: 0,
            fontSize: 'var(--kds-text-m-size)',
            lineHeight: 'var(--kds-text-m-line)',
            color: 'var(--kds-fg-tertiary)',
            textWrap: 'pretty',
          }}
        >
          {cell.minority}
        </p>
      )}
    </div>
  )
}

const REVIEW_SRC_CSS = `
#review-src [role="tab"][aria-selected="true"] {
  background: var(--kds-accent) !important;
  border-color: var(--kds-accent) !important;
  color: var(--kds-fg-on-accent) !important;
  font-weight: var(--kds-weight-medium);
}
#review-src [role="tab"][aria-selected="false"] { color: var(--kds-fg-secondary) !important; }
`

export default function SlimeSearch() {
  /* 데이터가 오기 전에는 디자인의 자리표시자를 그대로 쓴다 — 레이아웃이 흔들리지 않고,
   * 원본 목업과 픽셀 대조가 가능한 상태가 유지된다. */
  const [data, setData] = useState<PageData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [qMarket, setQMarket] = useState('슬라임지나')  // 화면에 보이는 상호로 — API 가 조회 키로 환원
  const [qProduct, setQProduct] = useState('빠코볼')
  const page = (data ?? PLACEHOLDER) as PageData

  const search = (market: string, product: string) => {
    setBusy(true); setErr(null)
    fetchPage(market.trim(), product.trim())
      .then(setData)
      .catch((e) => setErr(String(e)))
      .finally(() => setBusy(false))
  }
  // 첫 진입에 한 번 — 빈 화면 대신 실제 데이터가 보이게.
  useEffect(() => { search(qMarket, qProduct) }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  // DCLogic: state = { src: 'both', open: {} }
  const [src, setSrc] = useState<Src>('both')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [igSort, setIgSort] = useState<keyof SortKey>('date')
  const [dcSort, setDcSort] = useState<keyof SortKey>('date')
  const [menu, setMenu] = useState<Panel | null>(null)
  const [igPage, setIgPage] = useState(1)
  const [dcPage, setDcPage] = useState(1)
  // 검색 결과가 갈리면 쪽 번호는 의미를 잃는다 — 새 목록은 1쪽부터.
  useEffect(() => { setIgPage(1); setDcPage(1) }, [data])

  // DCLogic: toggle(k)
  const toggle = (k: string) => setOpen((st) => ({ ...st, [k]: !st[k] }))

  // DCLogic: renderVals()
  const showIg = src === 'ig' || src === 'both'
  const showDc = src === 'dc' || src === 'both'
  const reviewCols = src === 'both' ? '1fr 1fr' : '1fr'
  const igSortLabel = IG_SORTS.find((o) => o.key === igSort)?.label ?? '최신순'
  const dcSortLabel = DC_SORTS.find((o) => o.key === dcSort)?.label ?? '최신순'
  const igSorted = sortReviews(page.igReviews, igSort)
  const dcSorted = sortReviews(page.dcReviews, dcSort)
  const igPages = Math.max(1, Math.ceil(igSorted.length / PAGE_SIZE))
  const dcPages = Math.max(1, Math.ceil(dcSorted.length / PAGE_SIZE))
  /* 쪽 수가 줄어드는 경우(정렬 바꿈이 아니라 목록 자체가 짧아진 경우)에도 빈 쪽이 나오지
   * 않게 렌더 시점에 조인다 — state 를 고치는 대신 읽을 때 clamp 한다. */
  const igCur = Math.min(igPage, igPages)
  const dcCur = Math.min(dcPage, dcPages)
  const igReviews = igSorted.slice((igCur - 1) * PAGE_SIZE, igCur * PAGE_SIZE)
  const dcReviews = dcSorted.slice((dcCur - 1) * PAGE_SIZE, dcCur * PAGE_SIZE)

  // DCLogic: sortItems(panel, opts)
  const sortItems = (
    cur: string,
    opts: readonly { key: keyof SortKey; label: string }[],
    pick: (k: keyof SortKey) => void,
  ) =>
    opts.map((o) => ({
      ...o,
      fw: cur === o.key ? 'var(--kds-weight-medium)' : 'var(--kds-weight-regular)',
      color: cur === o.key ? 'var(--kds-accent-text)' : 'var(--kds-fg-secondary)',
      pick: () => {
        pick(o.key)
        setMenu(null)
      },
    }))

  const specRow = {
    display: 'grid',
    gridTemplateColumns: '88px 1fr',
    gap: 16,
    padding: 20,
    borderBottom: '1px solid var(--kds-border)',
  } as const
  const specLabel = {
    fontSize: 'var(--kds-text-l-size)',
    lineHeight: 'var(--kds-text-l-line)',
    color: 'var(--kds-fg-tertiary)',
  } as const
  const specValue = {
    fontSize: 'var(--kds-text-xl-size)',
    lineHeight: 'var(--kds-text-xl-line)',
  } as const
  const sortMenu = {
    position: 'absolute',
    right: 0,
    top: 'calc(100% + 6px)',
    minWidth: 132,
    background: 'var(--kds-bg)',
    border: '1px solid var(--kds-border)',
    borderRadius: 'var(--kds-radius-8)',
    boxShadow: 'var(--kds-shadow-gray-100)',
    overflow: 'hidden',
    zIndex: 20,
    display: 'flex',
    flexDirection: 'column',
  } as const

  return (
    <div style={{ minHeight: '100vh', background: 'var(--kds-bg)', paddingBottom: 80 }}>
      <style>{REVIEW_SRC_CSS}</style>

      <header
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 20,
          background: 'var(--kds-bg)',
          borderBottom: '1px solid var(--kds-border)',
        }}
      >
        <div
          style={{
            maxWidth: 1200,
            margin: '0 auto',
            padding: '20px 24px 0',
            display: 'flex',
            flexDirection: 'column',
            gap: 20,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 24,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div
                style={{
                  width: 54,
                  height: 36,
                  overflow: 'hidden',
                  position: 'relative',
                  flex: 'none',
                }}
              >
                <img
                  src="/brand/logo.png"
                  alt="슬믈리에"
                  style={{ position: 'absolute', width: 127, height: 127, left: -37, top: -30 }}
                />
              </div>
              <img
                src="/brand/wordmark.png"
                alt="슬믈리에"
                style={{ height: 26, width: 'auto', display: 'block' }}
              />
            </div>
          </div>

          <div
            style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 24 }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Input
                  label="마켓"
                  size="l"
                  placeholder="슬라임 마켓 이름 (선택)"
                  value={qMarket}
                  onChange={(e) => setQMarket(e.target.value)}
                />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <Input
                  label="제품"
                  size="l"
                  placeholder="슬라임 제품 이름 (선택)"
                  value={qProduct}
                  onChange={(e) => setQProduct(e.target.value)}
                />
              </div>
              <Button
                hierarchy="primary"
                size="l"
                disabled={busy}
                onClick={() => search(qMarket, qProduct)}
              >
                {busy ? '검색 중…' : '검색하기'}
              </Button>
            </div>
          </div>
        </div>
      </header>

      <main style={{ maxWidth: 1200, margin: '0 auto', padding: '0 24px' }}>
        {err && (
          <div
            role="alert"
            style={{
              margin: '16px 0',
              padding: '12px 16px',
              borderRadius: 'var(--kds-radius-8)',
              background: 'var(--kds-red-100)',
              color: 'var(--kds-red-800)',
              fontSize: 'var(--kds-text-l-size)',
            }}
          >
            불러오지 못했어요 — {err} (API 서버가 켜져 있는지 확인해주세요)
          </div>
        )}
        <section
          style={{
            padding: '40px 0 32px',
            display: 'grid',
            gridTemplateColumns: '1fr auto',
            gap: 40,
            alignItems: 'end',
            borderBottom: '1px solid var(--kds-border)',
          }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <div
                style={{
                  width: 48,
                  height: 48,
                  flex: 'none',
                  borderRadius: '50%',
                  background: 'var(--kds-bg-subtle)',
                  border: '1px solid var(--kds-border)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 'var(--kds-text-m-size)',
                  color: 'var(--kds-fg-tertiary)',
                  textAlign: 'center',
                  lineHeight: 1.2,
                  overflow: 'hidden',
                }}
              >
                {page.marketLogo ? (
                  <img
                    src={page.marketLogo}
                    alt={page.market}
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                  />
                ) : page.marketMonogram ? (
                  /* 로고 파일이 없으면 모노그램으로 degrade — 삭제=철회 성질 보존(ADR-0010) */
                  <span style={{ fontSize: 'var(--kds-text-xl-size)', fontWeight: 'var(--kds-weight-bold)' }}>
                    {page.marketMonogram}
                  </span>
                ) : (
                  <>
                    마켓
                    <br />
                    이미지
                  </>
                )}
              </div>
              <span
                style={{
                  fontSize: 'var(--kds-text-l-size)',
                  lineHeight: 'var(--kds-text-l-line)',
                  color: 'var(--kds-accent-text)',
                  fontWeight: 'var(--kds-weight-medium)',
                }}
              >
                {page.marketUrl ? (
                  <a href={page.marketUrl} target="_blank" rel="noreferrer noopener">
                    {page.market}
                  </a>
                ) : (
                  page.market
                )}
              </span>
            </div>
            <h1
              style={{
                margin: 0,
                fontSize: 'var(--kds-title-xl-size)',
                lineHeight: 'var(--kds-title-xl-line)',
                fontWeight: 'var(--kds-weight-bold)',
                letterSpacing: 'var(--kds-tracking)',
                color: 'var(--kds-fg)',
                textWrap: 'balance',
              }}
            >
              {page.product}
            </h1>
          </div>
        </section>

        <section style={{ padding: '48px 0 32px' }}>
          <div
            style={{
              display: 'grid',
              /* 판매자 게시물은 한때 **화면 가로 중앙까지**(`50%`) 왔으나 420px 상한으로 줄였다
               * (사용자 결정 2026-08-06, 앞선 지시를 대체). 4/5 비율이라 폭이 곧 높이다 —
               * 1440px 화면에서 576×720 이 되어 옆의 제품 정보 카드(~390)보다 330px 이나 길었고,
               * 그 빈 칸이 그대로 '리뷰 요약' 앞의 여백으로 보였다. 420 이면 525 로 내려와
               * 남는 세로가 ~135px. 상한이라 좁은 창에서는 `minmax` 가 알아서 줄인다. */
              gridTemplateColumns: 'minmax(0, 420px) 1fr',
              gap: 24,
              alignItems: 'start',
            }}
          >
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div
                style={{
                  aspectRatio: '4/5',
                  borderRadius: 'var(--kds-radius-12)',
                  background: 'var(--kds-gray-900)',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 10,
                }}
              >
                {page.media.embedUrl ? (
                  /* 판매자 '본인' 게시물만 임베드한다(ADR-0009). 바이트는 인스타가 서빙 —
                   * 우리가 받아서 다시 뿌리지 않는다. 식별자가 없으면 이 분기 자체가 죽는다. */
                  <iframe
                    src={page.media.embedUrl}
                    title={`${page.market} ${page.product} 판매자 게시물`}
                    style={{ width: '100%', height: '100%', border: 0,
                             borderRadius: 'var(--kds-radius-12)', background: 'var(--kds-white)' }}
                    loading="lazy"
                  />
                ) : (
                  <>
                    <Icon name="play" size={32} color="var(--kds-white)" />
                    <span style={{ fontSize: 'var(--kds-text-l-size)', color: 'var(--kds-white)' }}>
                      {page.media.caption}
                    </span>
                    <span
                      style={{ fontSize: 'var(--kds-text-m-size)', color: 'var(--kds-gray-400)' }}
                    >
                      {page.media.spec}
                    </span>
                  </>
                )}
              </div>
            </div>

            <div
              style={{
                border: '1px solid var(--kds-border)',
                borderRadius: 'var(--kds-radius-12)',
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  padding: '16px 20px',
                  borderBottom: '1px solid var(--kds-border)',
                  fontSize: 'var(--kds-title-xs-size)',
                  lineHeight: 'var(--kds-title-xs-line)',
                  fontWeight: 'var(--kds-weight-bold)',
                }}
              >
                제품 정보
              </div>
              <div style={specRow}>
                <span style={specLabel}>풀 조합</span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {page.spec.glues.map((g) => (
                      <Chip key={g}>{g}</Chip>
                    ))}
                  </div>
                </div>
              </div>
              <div style={specRow}>
                <span style={specLabel}>향</span>
                <span style={specValue}>{page.spec.scent}</span>
              </div>
              <div style={specRow}>
                <span style={specLabel}>종류</span>
                <span style={specValue}>{page.spec.slimeType}</span>
              </div>
              <div style={specRow}>
                <span style={specLabel}>질감</span>
                <span style={{ ...specValue, lineHeight: PROSE_LINE }}>{page.spec.texture}</span>
              </div>
              <div
                style={{
                  padding: '16px 20px',
                  background: 'var(--kds-bg-subtle)',
                  fontSize: 'var(--kds-text-m-size)',
                  lineHeight: 'var(--kds-text-m-line)',
                  color: 'var(--kds-fg-secondary)',
                }}
              >
                판매자 제공 정보 · 리뷰 요약과는 별개예요
              </div>
            </div>
          </div>
        </section>

        <section style={{ padding: '0 0 48px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <h2
              style={{
                margin: 0,
                fontSize: 'var(--kds-title-m-size)',
                lineHeight: 'var(--kds-title-m-line)',
                fontWeight: 'var(--kds-weight-bold)',
                letterSpacing: 'var(--kds-tracking)',
              }}
            >
              리뷰 요약
            </h2>
            <Badge tone="solid">AI 요약</Badge>
          </div>
          <p
            style={{
              margin: '0 0 24px',
              fontSize: 'var(--kds-text-l-size)',
              lineHeight: 'var(--kds-text-l-line)',
              color: 'var(--kds-fg-secondary)',
            }}
          >
            서포터 게시물을 제외한 리뷰를 바탕으로 AI가 작성했어요.
          </p>

          {/* 요약 카드 — 왼쪽 링 게이지 + 오른쪽 축별 2열 그리드(디자인 131–169줄).
            * 카드 안에 '통합 요약' 제목 줄은 두지 않는다: 디자인에 없고, 바로 위 섹션 제목이
            * 이미 'AI 요약' 배지를 달고 "AI가 작성했어요"라고 적어 둔다 — 한 화면에서 같은 말을
            * 두 번 하는 자리였다(같은 이유로 2026-08-06 에 'AI 통합' 배지도 뺐다).
            *
            * 요약이 아직 없으면 링도 숫자도 그리지 않는다. 문장이 없는데 점수만 뜨면 그 숫자가
            * 어디서 왔는지 화면에 근거가 하나도 남지 않는다. */}
          <div
            style={{
              border: '1px solid var(--kds-border)',
              borderRadius: 'var(--kds-radius-12)',
              padding: 32,
              marginBottom: 40,
              display: 'grid',
              gridTemplateColumns: page.summary.all.length ? '300px 1fr' : '1fr',
              gap: 40,
              alignItems: 'start',
            }}
          >
            {page.summary.all.length ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
                  <div style={{ position: 'relative', width: 132, height: 132, flex: 'none' }}>
                    <svg
                      width="132"
                      height="132"
                      viewBox="0 0 132 132"
                      aria-hidden="true"
                      style={{ display: 'block', transform: 'rotate(-90deg)' }}
                    >
                      <circle
                        cx="66"
                        cy="66"
                        r="58"
                        fill="none"
                        stroke="var(--kds-bg-muted)"
                        strokeWidth="10"
                      />
                      <circle
                        cx="66"
                        cy="66"
                        r="58"
                        fill="none"
                        stroke="var(--kds-accent)"
                        strokeWidth="10"
                        strokeLinecap="round"
                        strokeDasharray="364.4"
                        strokeDashoffset={ringOffset(page.summary.score.overall)}
                      />
                    </svg>
                    <span
                      style={{
                        position: 'absolute',
                        inset: 0,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: 40,
                        lineHeight: 1,
                        fontWeight: 'var(--kds-weight-bold)',
                        letterSpacing: 'var(--kds-tracking)',
                      }}
                    >
                      {page.summary.score.overall}
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <span
                      style={{
                        fontSize: 'var(--kds-title-m-size)',
                        lineHeight: 'var(--kds-title-m-line)',
                        fontWeight: 'var(--kds-weight-bold)',
                        letterSpacing: 'var(--kds-tracking)',
                      }}
                    >
                      {page.summary.score.label}
                    </span>
                    {/* 근거 건수 줄은 요약 본문보다 작게(사용자 지시 2026-08-07) — `text-m`(12px) +
                      * 3차 색. 읽을 것은 오른쪽 축별 문장이고 이건 그 근거 표시라, 본문과 같은
                      * 크기면 마지막 문장처럼 읽힌다. */}
                    <span
                      style={{
                        fontSize: 'var(--kds-text-m-size)',
                        lineHeight: 'var(--kds-text-m-line)',
                        color: 'var(--kds-fg-tertiary)',
                      }}
                    >
                      {page.summary.allBasis}
                    </span>
                  </div>
                </div>
                <SummaryScores
                  rows={page.summary.all}
                  marketNote={page.summary.marketNote}
                  score={page.summary.score}
                />
              </>
            ) : (
              <p style={{ margin: 0, fontSize: 'var(--kds-text-xl-size)', lineHeight: PROSE_LINE }}>
                {NO_SUMMARY}
              </p>
            )}
          </div>

          {/* 펼치기 버튼은 제목 옆이 아니라 **다음 줄에 들여써서** 둔다(사용자 지시 2026-08-06).
            * 제목과 한 줄에 있으면 제목의 일부처럼 읽힌다 — 한 칸 들어가면 제목에 딸린 조작으로
            * 읽힌다. */}
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'flex-start',
              gap: 8,
              marginBottom: 16,
            }}
          >
            <h3
              style={{
                margin: 0,
                fontSize: 'var(--kds-title-s-size)',
                lineHeight: 'var(--kds-title-s-line)',
                fontWeight: 'var(--kds-weight-bold)',
                letterSpacing: 'var(--kds-tracking)',
              }}
            >
              평가 기준별 요약
            </h3>
            <Disclosure
              open={!!open.criteria}
              onClick={() => toggle('criteria')}
              labels={['펼치기', '접기']}
              controls="criteria-table"
              style={{ marginLeft: 12 }}
            />
          </div>

          <div
            id="criteria-table"
            hidden={!open.criteria}
            style={{
              border: '1px solid var(--kds-border)',
              borderRadius: 'var(--kds-radius-12)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: '150px 1fr 1fr 1fr',
                background: 'var(--kds-bg-subtle)',
                borderBottom: '1px solid var(--kds-border)',
              }}
            >
              <div
                style={{
                  padding: '14px 20px',
                  fontSize: 'var(--kds-text-m-size)',
                  color: 'var(--kds-fg-tertiary)',
                }}
              >
                기준
              </div>
              <div
                style={{
                  padding: '14px 20px',
                  borderLeft: '1px solid var(--kds-border)',
                  fontSize: 'var(--kds-text-l-size)',
                  fontWeight: 'var(--kds-weight-medium)',
                }}
              >
                인스타그램
              </div>
              <div
                style={{
                  padding: '14px 20px',
                  borderLeft: '1px solid var(--kds-border)',
                  fontSize: 'var(--kds-text-l-size)',
                  fontWeight: 'var(--kds-weight-medium)',
                }}
              >
                아모스갤
              </div>
              <div
                style={{
                  padding: '14px 20px',
                  borderLeft: '1px solid var(--kds-border)',
                  fontSize: 'var(--kds-text-l-size)',
                  fontWeight: 'var(--kds-weight-bold)',
                  color: 'var(--kds-accent-text)',
                }}
              >
                통합
              </div>
            </div>

            {CRITERIA.map((c) => {
              const cell = page.byCriterion(c)
              return (
                <div
                  key={c.key}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '150px 1fr 1fr 1fr',
                    borderBottom: '1px solid var(--kds-border)',
                  }}
                >
                  <div
                    style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 4 }}
                  >
                    <span
                      style={{
                        fontSize: 'var(--kds-text-xl-size)',
                        lineHeight: 'var(--kds-text-xl-line)',
                        fontWeight: 'var(--kds-weight-bold)',
                      }}
                    >
                      {c.ko}
                    </span>
                    {/* 디자인은 기준명 밑에 영문(`Texture`·`Sound`…)을 한 줄 더 뒀지만 **안 그린다**
                      * (사용자 지시 2026-08-07). 화면 문구는 전부 한국어고, 영문은 읽는 사람에게
                      * 새 정보를 주지 않는다. `en` 은 백엔드 `CRITERIA` 계약에 그대로 남는다 —
                      * 지운 건 표시뿐이다. */}
                    {/* 고객 응대·배송은 제품이 아니라 마켓 평가다(ADR-0015). 행 라벨 밑에
                      * 한 번만 적는다 — 세 칸에 각각 붙이면 같은 말이 줄마다 세 번 뜬다. */}
                    {c.scope === 'market' && (
                      <span
                        style={{
                          fontSize: 'var(--kds-text-m-size)',
                          lineHeight: 'var(--kds-text-m-line)',
                          color: 'var(--kds-fg-tertiary)',
                        }}
                      >
                        {page.summary.marketNote}
                      </span>
                    )}
                  </div>
                  <div style={{ padding: 20, borderLeft: '1px solid var(--kds-border)' }}>
                    <CriterionCell cell={cell.ig} />
                  </div>
                  <div style={{ padding: 20, borderLeft: '1px solid var(--kds-border)' }}>
                    <CriterionCell cell={cell.dc} />
                  </div>
                  <div
                    style={{
                      padding: 20,
                      borderLeft: '1px solid var(--kds-border)',
                      background: 'var(--kds-bg-subtle)',
                    }}
                  >
                    <CriterionCell cell={cell.all} strong />
                  </div>
                </div>
              )
            })}

            <div
              style={{
                padding: '16px 20px',
                fontSize: 'var(--kds-text-m-size)',
                lineHeight: 'var(--kds-text-m-line)',
                color: 'var(--kds-fg-tertiary)',
              }}
            >
              AI가 생성한 요약이라 원문과 다를 수 있어요. 개별 리뷰도 함께 확인해 주세요.
            </div>
          </div>
        </section>

        <section style={{ padding: '0 0 48px' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 6 }}>
            <h2
              style={{
                margin: 0,
                fontSize: 'var(--kds-title-m-size)',
                lineHeight: 'var(--kds-title-m-line)',
                fontWeight: 'var(--kds-weight-bold)',
                letterSpacing: 'var(--kds-tracking)',
              }}
            >
              커뮤니티 리뷰
            </h2>
          </div>
          <p
            style={{
              margin: '0 0 20px',
              fontSize: 'var(--kds-text-l-size)',
              lineHeight: 'var(--kds-text-l-line)',
              color: 'var(--kds-fg-secondary)',
            }}
          >
            이 영역에서는 두 출처의 리뷰가 섞이지 않아요.
          </p>

          <div id="review-src" style={{ marginBottom: 20 }}>
            <Tab
              items={[
                { value: 'both', label: '전체' },
                { value: 'ig', label: '인스타그램' },
                { value: 'dc', label: '아모스갤' },
              ]}
              value={src}
              onChange={(v) => setSrc(v as Src)}
              variant="secondary"
              fill={false}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: reviewCols, gap: 20 }}>
            {showIg && (
              <div
                style={{
                  border: '1px solid var(--kds-border)',
                  borderRadius: 'var(--kds-radius-12)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    padding: '16px 20px',
                    display: 'flex',
                    flexWrap: 'wrap',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: '8px 12px',
                    borderBottom: '1px solid var(--kds-border)',
                    background: 'var(--kds-bg-subtle)',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'baseline',
                      gap: 8,
                      minWidth: 0,
                    }}
                  >
                    <span
                      style={{
                        fontSize: 'var(--kds-title-xs-size)',
                        lineHeight: 'var(--kds-title-xs-line)',
                        fontWeight: 'var(--kds-weight-bold)',
                      }}
                    >
                      인스타그램
                    </span>
                    <span
                      style={{
                        fontSize: 'var(--kds-text-m-size)',
                        color: 'var(--kds-fg-tertiary)',
                      }}
                    >
                      {page.igCount}
                    </span>
                  </div>
                  <div style={{ position: 'relative', flex: 'none' }}>
                    <button
                      type="button"
                      onClick={() => setMenu((m) => (m === 'ig' ? null : 'ig'))}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        background: 'none',
                        border: 0,
                        padding: '4px 0',
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                        fontSize: 'var(--kds-text-l-size)',
                        fontWeight: 'var(--kds-weight-medium)',
                        color: 'var(--kds-fg)',
                      }}
                    >
                      <Icon name="arrow-up-down" size={16} color="var(--kds-fg)" />
                      {igSortLabel}
                    </button>
                    {menu === 'ig' && (
                      <div style={sortMenu}>
                        {sortItems(igSort, IG_SORTS, (k) => {
                          setIgSort(k)
                          setIgPage(1)   // 기준이 바뀌면 3쪽의 '최신순'과 3쪽의 '좋아요순'은 남남이다
                        }).map((s) => (
                          <button
                            key={s.key}
                            type="button"
                            className="sortMenuItem"
                            onClick={s.pick}
                            style={{
                              textAlign: 'left',
                              background: 'none',
                              border: 0,
                              padding: '10px 14px',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                              fontSize: 'var(--kds-text-l-size)',
                              fontWeight: s.fw,
                              color: s.color,
                            }}
                          >
                            {s.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {igReviews.map((r) => (
                  <div
                    key={r.id}
                    style={{
                      padding: 20,
                      borderBottom: '1px solid var(--kds-border)',
                      display: 'block',
                    }}
                  >
                    {/* 디자인엔 64px 썸네일 자리가 있었으나 **뺐다**(2026-08-06 사용자 결정).
                      * 채울 방법이 없다: IG CDN URL 은 서명이 걸려 수일 내 만료되고, 만료를
                      * 피하려면 다운로드·재호스팅해야 하는데 사용자 후기 미디어는 리뷰의 대상
                      * 저작물이라 ADR-0010 이 명시적으로 금지한다(예외는 마켓 아바타 하나뿐).
                      * 영원히 빈 사각형이 남느니 자리를 없앤다. */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
                        <span
                          style={{
                            fontSize: 'var(--kds-text-l-size)',
                            fontWeight: 'var(--kds-weight-medium)',
                          }}
                        >
                          {r.account}
                        </span>
                        <span
                          style={{
                            fontSize: 'var(--kds-text-m-size)',
                            color: 'var(--kds-fg-tertiary)',
                          }}
                        >
                          {r.date}
                        </span>
                      </div>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 'var(--kds-text-l-size)',
                          lineHeight: 'var(--kds-text-l-line)',
                          color: 'var(--kds-fg-secondary)',
                          textWrap: 'pretty',
                        }}
                      >
                        {r.body}
                      </p>
                      <a href={r.url} style={{ fontSize: 'var(--kds-text-m-size)' }}>
                        원문 보기
                      </a>
                    </div>
                  </div>
                ))}

                <Pager
                  cur={igCur}
                  total={igPages}
                  onPick={setIgPage}
                  label="인스타그램 후기 페이지"
                />
              </div>
            )}

            {showDc && (
              <div
                style={{
                  border: '1px solid var(--kds-border)',
                  borderRadius: 'var(--kds-radius-12)',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    padding: '16px 20px',
                    display: 'flex',
                    flexWrap: 'wrap',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    gap: '8px 12px',
                    borderBottom: '1px solid var(--kds-border)',
                    background: 'var(--kds-bg-subtle)',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }}>
                    <span
                      style={{
                        fontSize: 'var(--kds-title-xs-size)',
                        lineHeight: 'var(--kds-title-xs-line)',
                        fontWeight: 'var(--kds-weight-bold)',
                      }}
                    >
                      디시인사이드 아모스 갤러리
                    </span>
                    <span
                      style={{
                        fontSize: 'var(--kds-text-m-size)',
                        color: 'var(--kds-fg-tertiary)',
                      }}
                    >
                      {page.dcCount}
                    </span>
                  </div>
                  <div style={{ position: 'relative', flex: 'none' }}>
                    <button
                      type="button"
                      onClick={() => setMenu((m) => (m === 'dc' ? null : 'dc'))}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        background: 'none',
                        border: 0,
                        padding: '4px 0',
                        cursor: 'pointer',
                        fontFamily: 'inherit',
                        fontSize: 'var(--kds-text-l-size)',
                        fontWeight: 'var(--kds-weight-medium)',
                        color: 'var(--kds-fg)',
                      }}
                    >
                      <Icon name="arrow-up-down" size={16} color="var(--kds-fg)" />
                      {dcSortLabel}
                    </button>
                    {menu === 'dc' && (
                      <div style={sortMenu}>
                        {sortItems(dcSort, DC_SORTS, (k) => {
                          setDcSort(k)
                          setDcPage(1)
                        }).map((s) => (
                          <button
                            key={s.key}
                            type="button"
                            className="sortMenuItem"
                            onClick={s.pick}
                            style={{
                              textAlign: 'left',
                              background: 'none',
                              border: 0,
                              padding: '10px 14px',
                              cursor: 'pointer',
                              fontFamily: 'inherit',
                              fontSize: 'var(--kds-text-l-size)',
                              fontWeight: s.fw,
                              color: s.color,
                            }}
                          >
                            {s.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                {dcReviews.map((r) => (
                  <div
                    key={r.id}
                    style={{
                      padding: 20,
                      borderBottom: '1px solid var(--kds-border)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 8,
                    }}
                  >
                    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
                      <span
                        style={{
                          fontSize: 'var(--kds-text-l-size)',
                          fontWeight: 'var(--kds-weight-medium)',
                        }}
                      >
                        {r.title}
                      </span>
                      <span
                        style={{
                          fontSize: 'var(--kds-text-m-size)',
                          color: 'var(--kds-fg-tertiary)',
                        }}
                      >
                        {r.meta}
                      </span>
                    </div>
                    <p
                      style={{
                        margin: 0,
                        fontSize: 'var(--kds-text-l-size)',
                        lineHeight: 'var(--kds-text-l-line)',
                        color: 'var(--kds-fg-secondary)',
                        textWrap: 'pretty',
                      }}
                    >
                      {r.body}
                    </p>
                    <div
                      style={{
                        display: 'flex',
                        gap: 14,
                        alignItems: 'center',
                        fontSize: 'var(--kds-text-m-size)',
                        color: 'var(--kds-fg-tertiary)',
                      }}
                    >
                      <span>댓글 {r.comments}</span>
                      <span>추천 {r.votes}</span>
                      <a href={r.url} style={{ fontSize: 'var(--kds-text-m-size)' }}>
                        원문 보기
                      </a>
                    </div>
                  </div>
                ))}

                <Pager
                  cur={dcCur}
                  total={dcPages}
                  onPick={setDcPage}
                  label="아모스 갤러리 후기 페이지"
                />
              </div>
            )}
          </div>
        </section>
      </main>
    </div>
  )
}
