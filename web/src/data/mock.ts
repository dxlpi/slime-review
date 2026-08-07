/* 디자인 자리표시자 — `Slime Search.dc.html` 의 문자열을 **그대로** 옮긴 것.
 *
 * 화면을 디자인과 1:1 로 대조할 수 있게, 문구를 고치지 않고 여기 모아둔다.
 * FastAPI 가 붙으면 이 모듈만 실제 응답으로 갈아끼우면 된다 —
 * 화면 코드는 이 shape 만 알고 있으면 되므로 마크업은 손대지 않는다.
 */

/* ⚠️ `key` 는 백엔드 `consolidated_view.CRITERIA` 와 **같은 값이어야 한다**.
 * 한 리스트가 추출 스키마·요약 프롬프트·이 표를 동시에 움직인다(ADR-0011) — 여기서 키를
 * 새로 지으면 표가 조용히 빈칸으로 degrade 한다(에러가 안 난다). 순서도 백엔드와 같게 둔다.
 *
 * `scope` 는 그 줄이 **무엇에 대한 평가인가**다(ADR-0015). 고객 응대·배송은 제품이 아니라
 * 마켓 것이라 마켓당 한 벌만 만들고 모든 제품 페이지가 그걸 빌려 쓴다 — 화면은 그 줄에
 * '이 마켓 전체 기준'이라고 말해 줘야 한다. 값 자체는 백엔드가 실어 보낸다(여기 상수는
 * 목 데이터·행 순서용). */
export const CRITERIA = [
  { key: 'texture', ko: '질감', en: 'Texture', scope: 'product' },
  { key: 'scent', ko: '향', en: 'Scent', scope: 'product' },
  { key: 'sound', ko: '소리', en: 'Sound', scope: 'product' },
  { key: 'longevity', ko: '지속력', en: 'Longevity', scope: 'product' },
  { key: 'cs', ko: '고객 응대', en: 'Customer service', scope: 'market' },
  { key: 'shipping', ko: '배송', en: 'Shipping', scope: 'market' },
] as const

export type Criterion = (typeof CRITERIA)[number]
export type Scope = 'product' | 'market'

/** 마켓 축 줄에 붙는 설명. 제품 페이지에서 이 두 줄만 모집단이 다르다는 걸 말한다. */
export const MARKET_SCOPE_NOTE = '이 마켓 전체 기준이에요'

/** 기준 한 칸 — 다수 의견과 소수 반론이 **구조로** 갈린다(ADR-0014). 둘 다 null 이면 미언급. */
export type Cell = { verdict: string | null; minority: string | null }

/* ⚠️ 기준 줄에 배지·라벨을 붙이지 말 것(사용자 결정 2026-08-07). 건수('인스타 27 · 아모스갤 6')·
 *    정서 분포('갈림 19:5')·'인스타만' 을 붙여 봤고 전부 걷어냈다 — 다수/소수는 이미 verdict/
 *    minority 두 칸이 문장으로 말하고 있어서 같은 말이 줄마다 두 번 붙었다.
 *    `scope` 는 그 철회의 예외가 아니다: 건수가 아니라 **무엇에 대한 평가인지**라서, 안 쓰면
 *    읽는 사람이 배송 평가를 이 제품 것으로 오해한다. */
export type SummaryRow = Cell & { key: string; ko: string; en: string; scope: Scope }
export type CriterionRow = { ig: Cell; dc: Cell; all: Cell; scope: Scope }

/** 요약 카드의 점수 — 링 게이지(`overall`·`label`)와 축별 숫자(`byKey`). 값은 문자열이다:
 *  '7.8' 처럼 소수 한 자리로 **표시된 그대로** 다루고, 화면에서 반올림하지 않는다. */
export type SummaryScore = { overall: string; label: string; byKey: Record<string, string> }

/* ⚠️ 임시 자리표시자 — 점수 산출 로직이 **아직 없다**(사용자 결정 2026-08-07: 곧 도입).
 *    지금 화면에 뜨는 7.8 / 8.4 는 디자인 원본(`Slime Search.dc.html` 의 `scoreList`)의
 *    숫자 그대로고, 실데이터와 아무 관계가 없다.
 *
 *    산출이 들어오면 **여기 한 곳만** 지우면 된다: `api.ts` 의 `toPageData` 가 이 상수를
 *    폴백으로 쓰는 자리와 이 정의, 둘뿐이다. 점수를 화면에서 계산하지 말 것 — 정서 집계는
 *    백엔드(`consolidated_view.criterion_stats`)가 이미 갖고 있고, 두 벌이 되면 카드와
 *    기준표가 다른 말을 한다.
 *
 *    ⚠️ 실제 점수가 생길 때: 두 출처를 **하나로 평균낸 숫자만** 내보내지 말 것. 소스 편향
 *       (인스타 긍정 / 디시 부정)은 보정 대상이 아니라 이 프로젝트의 존재 이유다(CLAUDE.md
 *       1급 규칙) — 통합 점수를 쓰더라도 출처별 값과 갭이 같이 보여야 한다. */
export const PLACEHOLDER_SCORE: SummaryScore = {
  overall: '7.8',
  label: '추천해요',
  byKey: {
    texture: '8.4',
    scent: '8.1',
    sound: '7.6',
    longevity: '7.2',
    // 디자인의 `scoreList` 는 제품 축 넷뿐이다 — 마켓 축 둘은 같은 자리표시자 성격으로 채운다.
    cs: '7.9',
    shipping: '7.4',
  },
}

/** 지표가 하나도 없는 정렬 키 — 자리표시자 후기용(`api.ts` 의 '모름' 규약과 같은 값). */
const NO_SORT = {
  date: '',
  likes: Number.NEGATIVE_INFINITY,
  views: Number.NEGATIVE_INFINITY,
  votes: Number.NEGATIVE_INFINITY,
}

export const page = {
  market: '마켓명 자리',
  marketLogo: null as string | null, // null → 디자인의 '마켓\n이미지' 자리 원
  marketUrl: null as string | null,
  marketMonogram: null as string | null,
  product: '제품명 자리 — 최대 두 줄까지 들어갑니다',

  media: {
    caption: '공식 영상 자리',
    spec: '1080 × 1350 · 4:5',
    embedUrl: null as string | null,
  },

  spec: {
    glues: ['풀 1 자리', '풀 2 자리'],
    scent: '향 이름 · 계열 자리',
    slimeType: '종류 자리 (폼볼 · 디폼 · 클라우드)',
    texture: '질감 설명 자리 (버터 · 클리어 · 크런치)',
  },

  /* 요약 카드는 **축별 줄 목록**이다(ADR-0014). 예전엔 문단 한 덩어리였는데 질감·향·소리·
   * 지속력이 한 문단에 뒤섞여 축 경계가 안 보였다. 줄마다 라벨이 붙고, 다수 의견(verdict)과
   * 소수 반론(minority)이 따로 선다. */
  summary: {
    all: [
      { key: 'texture', ko: '질감', en: 'Texture', scope: 'product', verdict: '두 출처를 합친 다수 의견 자리. 한 문장.', minority: '일부는 다르게 본다는 소수 의견 자리.' },
      { key: 'scent', ko: '향', en: 'Scent', scope: 'product', verdict: '향에 대한 다수 의견 자리. 한 문장.', minority: null },
      { key: 'shipping', ko: '배송', en: 'Shipping', scope: 'market', verdict: '이 마켓의 배송에 대한 다수 의견 자리.', minority: null },
    ] as SummaryRow[],
    allBasis: '인스타 —건 + 아모스갤 —건 기반',
    ig: [
      { key: 'texture', ko: '질감', en: 'Texture', scope: 'product', verdict: '인스타그램 후기만으로 본 다수 의견 자리.', minority: null },
    ] as SummaryRow[],
    igBasis: '출처 —건 기반',
    dc: [
      { key: 'texture', ko: '질감', en: 'Texture', scope: 'product', verdict: '아모스갤 글만으로 본 다수 의견 자리.', minority: null },
    ] as SummaryRow[],
    dcBasis: '출처 —건 기반',
    marketNote: MARKET_SCOPE_NOTE,
    score: PLACEHOLDER_SCORE,
  },

  /* 기준별 요약 — 디자인은 `{{ c.ko }} 관련 … 요약 자리` 로 기준명을 끼워 넣는다. */
  byCriterion: (c: Criterion): CriterionRow => ({
    ig: { verdict: `${c.ko} 관련 인스타그램 다수 의견 자리.`, minority: null },
    dc: { verdict: `${c.ko} 관련 아모스갤 다수 의견 자리.`, minority: null },
    all: { verdict: `두 출처를 합친 ${c.ko} 다수 의견 자리.`, minority: '일부는 다르게 본다는 소수 의견 자리.' },
    scope: c.scope,
  }),

  /* 자리표시자에도 `sortKey` 가 있어야 한다 — 화면의 정렬은 데이터가 오기 전에도 돌고,
   * 없으면 첫 렌더에서 undefined 를 읽는다. 값은 전부 '모름'(-Infinity)이라 순서가 안 바뀐다. */
  igReviews: [
    { id: 1, account: '@계정명 자리', date: 'YYYY.MM.DD', body: '리뷰 본문 자리. 인스타그램 게시물 캡션이 그대로 들어가요.', url: '#', sortKey: NO_SORT },
    { id: 2, account: '@계정명 자리', date: 'YYYY.MM.DD', body: '리뷰 본문 자리. 인스타그램 게시물 캡션이 그대로 들어가요.', url: '#', sortKey: NO_SORT },
    { id: 3, account: '@계정명 자리', date: 'YYYY.MM.DD', body: '리뷰 본문 자리. 인스타그램 게시물 캡션이 그대로 들어가요.', url: '#', sortKey: NO_SORT },
  ],
  igCount: '—건',

  dcReviews: [
    { id: 1, title: '글 제목 자리', meta: 'ㅇㅇ · YYYY.MM.DD', body: '본문 자리. 갤러리 글 원문이 들어가고, 댓글과 추천 수를 아래에 표기해요.', comments: '—', votes: '—', url: '#', sortKey: NO_SORT },
    { id: 2, title: '글 제목 자리', meta: 'ㅇㅇ · YYYY.MM.DD', body: '본문 자리. 갤러리 글 원문이 들어가고, 댓글과 추천 수를 아래에 표기해요.', comments: '—', votes: '—', url: '#', sortKey: NO_SORT },
    { id: 3, title: '글 제목 자리', meta: 'ㅇㅇ · YYYY.MM.DD', body: '본문 자리. 갤러리 글 원문이 들어가고, 댓글과 추천 수를 아래에 표기해요.', comments: '—', votes: '—', url: '#', sortKey: NO_SORT },
  ],
  dcCount: '—건',
}
