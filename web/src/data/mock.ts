/* 디자인 자리표시자 — `Slime Search.dc.html` 의 문자열을 **그대로** 옮긴 것.
 *
 * 화면을 디자인과 1:1 로 대조할 수 있게, 문구를 고치지 않고 여기 모아둔다.
 * FastAPI 가 붙으면 이 모듈만 실제 응답으로 갈아끼우면 된다 —
 * 화면 코드는 이 shape 만 알고 있으면 되므로 마크업은 손대지 않는다.
 */

/* ⚠️ `key` 는 백엔드 `consolidated_view.CRITERIA` 와 **같은 값이어야 한다**.
 * 한 리스트가 추출 스키마·요약 프롬프트·이 표를 동시에 움직인다(ADR-0011) — 여기서 키를
 * 새로 지으면 표가 조용히 빈칸으로 degrade 한다(에러가 안 난다). 순서도 백엔드와 같게 둔다. */
export const CRITERIA = [
  { key: 'texture', ko: '질감', en: 'Texture' },
  { key: 'scent', ko: '향', en: 'Scent' },
  { key: 'sound', ko: '소리', en: 'Sound' },
  { key: 'longevity', ko: '지속력', en: 'Longevity' },
  { key: 'cs', ko: '고객 응대', en: 'Customer service' },
  { key: 'shipping', ko: '배송', en: 'Shipping' },
] as const

export type Criterion = (typeof CRITERIA)[number]

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

  summary: {
    all: '두 출처를 함께 반영한 요약 문단 자리. 인스타그램과 아모스갤의 평가가 엇갈리면 그 차이를 먼저 짚어드려요. 4–6문장 분량이 들어갑니다.',
    allBasis: '인스타 —건 + 아모스갤 —건 기반',
    ig: '인스타그램 리뷰만으로 만든 요약 문단 자리. 3–4문장 분량이에요.',
    igBasis: '출처 —건 기반',
    dc: '디시인사이드 아모스 갤러리 글만으로 만든 요약 문단 자리.',
    dcBasis: '출처 —건 기반',
  },

  /* 기준별 요약 — 디자인은 `{{ c.ko }} 관련 … 요약 자리` 로 기준명을 끼워 넣는다. */
  byCriterion: (c: Criterion) => ({
    ig: `${c.ko} 관련 인스타그램 요약 자리. 1–2문장.` as string | null,
    dc: `${c.ko} 관련 아모스갤 요약 자리. 1–2문장.` as string | null,
    all: `두 출처를 합친 ${c.ko} 요약 자리. 의견이 갈리는 지점도 함께 적어요.` as string | null,
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
