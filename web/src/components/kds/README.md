# components/kds — 디자인 번들에서 **잘라낸** KDS 컴포넌트

출처: `slime-search-platform-design/project/_ds/kyobo-design-system-kds-8f2246f5-…/_ds_bundle.js`
(format 4, namespace `KyoboDesignSystemKDS_8f2246`, 20개 중 **6개만** 사용)

번들 매니페스트의 `sourcePath` 는 전부 `components/<그룹>/<이름>.jsx` 꼴이다. 아래 표는
그룹과 번들 내 줄 범위만 적는다 — 경로를 그대로 쓰면 컨텍스트 경로 검사기가 **이 레포에 없는
파일**로 보고 CI 를 깨뜨린다(디자인 번들은 레포 밖에 있다).

| 파일 | 번들 그룹 | 번들 줄 |
|---|---|---|
| `Button.jsx` | actions | 11–107 |
| `Icon.jsx` | core | 108–163 |
| `Chip.jsx` | actions | 164–231 |
| `Badge.jsx` | display | 282–375 |
| `Input.jsx` | forms | 907–1021 |
| `Tab.jsx` | navigation | 1315–1378 |

## 여기서 창작하지 말 것

이 폴더는 **디자인 시스템의 사본**이다. props·스타일 값·분기 조건을 고치기 시작하면 디자인
번들과 대조가 불가능해지고, 그 순간 KDS 를 쓴다는 주장이 거짓이 된다. 슬라임 쪽 커스텀이
필요하면 토큰([`../../styles/slime-accent.css`](../../styles/slime-accent.css))이나 사용처에서
`style` prop 으로 덮어라.

## 잘라내면서 **불가피하게** 바꾼 것 (전부, 빠짐없이)

번들은 브라우저에 `<script>` 로 통째로 실려 전역 네임스페이스에 등록되는 형태였다. ESM
모듈로 옮기려면 그 배선만은 바꿔야 한다. 컴포넌트 **본문(props·스타일 객체·분기)은 한 글자도
건드리지 않았다**.

1. **번들 배선 제거** — `try { (() => { … })(); } catch` 래퍼와
   `Object.assign(__ds_scope, { X })` 등록을 `export default` 로 교체.
2. **컴포넌트 간 참조** — `__ds_scope.Icon` → `import Icon from './Icon'`.
   (Chip·Input 이 Icon 을 쓴다.)
3. **`_extends` 제거** — babel 이 넣은 `Object.assign` 폴리필이다. `{...rest}` 스프레드로 환원.
4. **`React.createElement(…)` → JSX** — 번들은 babel 컴파일 **결과물**이라 return 문만
   createElement 형태였다. 원본 `.jsx` 의 모습으로 되돌린 것이고, DOM 출력은 동일하다.
5. **`React` 전역 → import** — 번들은 `React` 가 전역에 있다고 가정했다.

## Icon 만은 한 가지 더 (`window.lucide` → npm)

원본 주석: *"Load … lucide.js once on the page."* — unpkg CDN 의 lucide UMD 빌드를 가리킨다.
아이콘을 CDN 전역에서 읽고, 없으면 **120ms 간격으로 폴링**하는 구조였다. 우리 앱엔 그
`<script>` 태그가 없으므로 `lucide` 패키지를 import 한다. 폴링도 같이 사라진다 — import 는
동기라서 기다릴 대상이 없다. 컴포넌트 API(`name`/`size`/`color`/`strokeWidth`)와 DOM 출력은 그대로.

## 알려진 충돌 (고치지 않음 — 사용처에서 덮어라)

액센트를 민트로 갈아끼우면서 **KDS 가 '액센트=파랑'을 전제하고 쓴 자리**들이 깨진다. 원본
보존이 우선이라 컴포넌트는 손대지 않았다. 대신 여기 전부 적어둔다.

### 1. `Chip selected` · `Tab variant="secondary"` 선택 상태 — **글자가 안 보인다** ⚠️

배경은 `--kds-gray-900`(거의 검정)인데 글자색이 `--kds-fg-on-accent` 다. KDS 기본값에서 그
토큰은 **흰색**이라 검정 배경 위에서 멀쩡했는데, 우리가 민트 위에 얹을 짙은 초록(`#10352A`)으로
덮었다. 그래서 **검정 위 짙은 초록** = 판독 불가.

디자인 파일(`Slime Search.dc.html`)도 이걸 알고 있었고, 출처 탭을 사용처에서 이렇게 덮었다:

```css
#review-src [role="tab"][aria-selected="true"] {
  background: var(--kds-accent) !important;   /* 검정 → 민트 */
  color: var(--kds-fg-on-accent) !important;  /* 그러면 짙은 초록이 맞는 색이 된다 */
}
```

즉 **선택 상태 배경을 민트로 바꾸는 것**이 이 디자인의 정답이다. 검정 배경을 유지하고 싶다면
그 자리에서 `style={{ color: 'var(--kds-white)' }}` 로 덮어라. 토큰을 고치면 안 된다 —
`--kds-fg-on-accent` 를 흰색으로 되돌리는 순간 민트 버튼의 글자가 흰색이 돼 대비가 무너진다.

### 2. `Badge tone="accent"` — 민트 배경 위 파란 글자

배경 `--kds-accent-subtle` 에 글자색 `--kds-blue-800`. 읽히기는 하지만 색 계열이 어긋난다.
이 조합이 필요하면 사용처에서 `style` 로 덮을 것.
