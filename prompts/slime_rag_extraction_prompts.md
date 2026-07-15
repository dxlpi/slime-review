# 슬라임 RAG — 추출 프롬프트 스펙 (1층 / 2층)

> LLM으로 **비정형 → 정형** 추출. 두 프롬프트 모두 공통 원칙: ① 명시된 것만 추출 ② 미언급은 `null`(절대 지어내지 않음) ③ 필드별 **근거 스니펫**(원문 짧게, 15자 내외) ④ 통제어휘는 목록 안에서, 새 표현은 `*_other`로 따로.

---

## 공통 통제어휘

**슬라임 종류 (TYPE_ENUM)** — 1층, 마켓이 정함
```
폼볼, 촉감류(점토), 디폼, 난사, 눈꽃, 지글리, 크런치, 빈백, 클라우드, 샤베트, 클리어, 버글리, 젤라또
```
하이브리드 가능(예: ["클라우드","지글리"]). 목록에 없으면 `type_other`에 원문 + `type` 은 null.

**질감 서술어 (FEEL_VOCAB)** — 2층, 사용자가 느낌
```
말랑, 말캉, 쫀득, 퐁신, 폭닥, 크리미, 로션크리미, 얄랑, 매트, 빳빳,
텐션감있는, 흐물거리는, 쳐지는, 흐름성있는
```
- 목록에 있으면 `feel`(멀티)에
- "~같은" 비유는 `feel_simile`(예: "생크림 같은")
- 목록에 없는 새 표현은 `feel_other`(나중에 어휘 확장용)

**발향(projection)**: `강함 | 적당 | 약함`
**손붙음/손묻음**: `있음 | 없음 | 약간`
**정서(sentiment)**: `pos | neu | neg`

---

## 1층 — 공식 제품 스펙 추출 (마켓 IG 캡션/이미지 → JSON)

### 시스템/지시
```
너는 한국 슬라임 마켓의 제품 안내 게시물에서 공식 스펙을 추출한다.
입력은 마켓이 직접 올린 게시물의 캡션(또는 스펙 카드 이미지에서 읽은 텍스트)이다.
한 게시물에 여러 제품이 있으면 제품별로 분리한다.
마켓이 명시한 정보만 추출하고, 없는 항목은 null로 둔다. 추측 금지.
slime_type 은 반드시 TYPE_ENUM 안에서 고르고, 없으면 type_other에 원문을 넣고 type은 null.
출력은 JSON 배열만. 다른 말 금지.
```

### 출력 스키마
```json
[{
  "product_name": "연유스무디",
  "official_scent": "진한 연유향",        // 마켓 표기 향료, 없으면 null
  "glue_composition": "투명풀 + 클레이 소량", // 풀 조합, 없으면 null
  "type": ["지글리"],                      // TYPE_ENUM (멀티 가능)
  "type_other": null,                      // enum에 없는 표기 원문
  "evidence": { "scent": "향: 진한 연유향", "glue": "베이스: 투명풀+클레이", "type": "지글리" }
}]
```

### Few-shot
입력 캡션:
```
[연유스무디] 🥛 5/20 재오픈
향: 진한 연유향 (호불호 적은 편)
베이스: 투명풀 + 클레이 소량
타입: 지글리
가격 12,000 / 50ml
```
출력:
```json
[{
  "product_name": "연유스무디",
  "official_scent": "진한 연유향",
  "glue_composition": "투명풀 + 클레이 소량",
  "type": ["지글리"],
  "type_other": null,
  "evidence": { "scent": "향: 진한 연유향", "glue": "베이스: 투명풀+클레이 소량", "type": "타입: 지글리" }
}]
```

---

## 2층 — 후기 추출 (디시/블로그/유튜브 후기 → JSON)

### 시스템/지시
```
너는 한국 슬라임 후기에서 사용자 경험을 구조화한다.
작성자가 '명시'한 내용만 추출하고, 안 나온 항목은 null. 추측·과장 금지.
각 필드에는 근거가 된 원문 조각을 evidence에 짧게(15자 내외) 넣는다.
feel 은 FEEL_VOCAB 안에서만, '~같은' 비유는 feel_simile, 목록에 없는 표현은 feel_other.
점수는 작성자가 직접 매긴 경우만 stated_rating, 아니면 null.
model_sentiment 는 텍스트 기반 추정이며 그렇게 라벨한다.
욕설/유해 표현이 있으면 flags.toxic 를 true 로.
출력은 JSON 객체만.
```

### 출력 스키마
```json
{
  "mentioned_market": "빈짱",      // 후기에 드러난 마켓 표기(초성/약칭 포함), 없으면 null
  "mentioned_product": "연유스무디", // 후기에 드러난 제품 표기(약칭 가능), 없으면 null
  "scent": {
    "perceived": "비누향",         // 사용자가 느낀 향
    "projection": "적당",          // 강함/적당/약함
    "vs_official_comment": "연유향이라는데 비누향", // 작성자가 공식향과 다르다고 직접 말한 경우만
    "sentiment": "neg",
    "evidence": "연유향이라더니 비누향"
  },
  "texture": {
    "feel": ["말랑","쫀득"],        // FEEL_VOCAB
    "feel_simile": "생크림 같은",    // ~같은 비유, 없으면 null
    "feel_other": null,            // 어휘 밖 새 표현
    "hand_stick": "없음",          // 손붙음
    "hand_residue": "없음",        // 손묻음
    "type_mentioned": null,        // 후기가 종류 언급 시(1층 교차검증), 없으면 null
    "sentiment": "pos",
    "evidence": "말랑쫀득 생크림같음 손에 안붙음"
  },
  "sound":      { "mentioned": true, "notes": "그냥저냥", "sentiment": "neu", "evidence": "소리는 그냥저냥" },
  "longevity":  null,
  "value":      { "krw": 12000, "sentiment": "pos", "evidence": "12000원 가성비 좋은듯" },
  "shipping_cs": null,
  "overall": {
    "stated_rating": null,         // 작성자가 명시한 점수만
    "model_sentiment": "pos",      // 모델 추정(라벨)
    "rebuy_intent": "있음",        // 있음/없음/미언급
    "summary": "향은 호불호, 질감·가성비는 호평"
  },
  "flags": { "toxic": false }
}
```

### Few-shot
입력 후기:
```
빈짱 연유스무디 후기! 발향은 적당한데 연유향이라더니 약간 비누향 나요ㅋㅋ
질감은 완전 말랑쫀득에 생크림 같은 느낌? 손에 안 붙고 묻음도 없어요.
소리는 그냥저냥. 12000원이면 가성비 좋은듯. 재구매 ㅇㅇ
```
출력: (위 스키마 예시와 동일한 값)

---

## 파이프라인 메모

- **개체연결은 추출 전/후 별도 단계.** 2층 프롬프트는 `mentioned_market` / `mentioned_product`(초성·약칭 그대로)만 뽑고, 그걸 KB와 매칭해 정규 `product_ref` + 확신도를 붙인다. (제품명 교차신호 → 문맥 → LLM 판정 → 보류)
- **향 불일치(diverges_from_official)** 는 LLM이 판단하지 않고 **조인 단계에서 계산**: 2층 `scent.perceived` vs 1층 `official_scent` 비교. 단, 작성자가 직접 다르다고 말한 건 `vs_official_comment` 로 보존.
- **evidence 길이 제한**(15자 내외)으로 원문 통째 복제 방지(저작권). RAG 인용 시에도 이 스니펫만.
- **temperature 낮게**(0~0.2), JSON 강제, 파싱 실패 시 1회 재시도 → 관측성 로그에 기록.
- **feel_other / type_other 수집분**은 주기적으로 검토해 어휘에 승격(어휘가 자라는 구조).
