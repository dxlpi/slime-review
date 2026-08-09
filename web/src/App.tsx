/* 화면 2개 — 공개 검색 화면과 로컬 전용 1층 스펙 검수 도구(ADR-0016).
 *
 * 라우팅은 **의존성 추가 없이** `location.pathname` 분기다. react-router 는 설치돼 있지
 * 않고, 화면 둘에 라우터를 들이는 건 과하다. 검수 화면은 로컬에서만 열리고 링크로
 * 공유되지도 않으므로 히스토리 관리가 필요 없다.
 */
import SlimeSearch from './screens/SlimeSearch'
import SpecReview from './screens/SpecReview'

export default function App() {
  // 끝 슬래시를 벗긴다 — `/review/` 로 들어오면 분기를 못 타고 검색 화면이 뜬다. 주소창에
  // 손으로 치는 로컬 도구라 그 오타가 실제로 난다(그때 화면은 정상으로 보여서 원인이 안 보인다).
  const path = window.location.pathname.replace(/\/+$/, '')
  return path === '/review' ? <SpecReview /> : <SlimeSearch />
}
