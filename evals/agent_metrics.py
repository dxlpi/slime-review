#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 코딩 도구 생산성 **정량** 측정 (평가 하드게이트 #2 · AI-Ready 루브릭 Cat G).

[BUILD_LOG.md](../BUILD_LOG.md) 는 phase 마다 '무엇을/핵심 프롬프트/AI생성 vs 사람수정/소요시간'
을 **서술로** 남긴다. 그건 무슨 일이 있었는지는 알려주지만 '세션당 도구 호출 몇 회' 같은 건
알려주지 않는다. 이 스크립트가 그 축을 센다 — 서술을 대체하는 게 아니라 옆에 숫자를 붙인다.

입력: `~/.claude/projects/<인코딩된-레포경로>/*.jsonl` (Claude Code 세션 로그, **레포 밖**)
출력: 집계 숫자만. 대화 내용·파일 내용·프롬프트 원문은 **읽지도 내보내지도 않는다** —
      세는 건 메시지의 역할·도구 이름·usage 필드뿐이다.

⚠️ 로그는 레포 밖에 있고 커밋되지 않는다. 다른 기계에서 돌리면 숫자가 다르게 나오는 게 정상이다
   (그 기계의 세션이 다르니까). 그래서 결과를 문서에 적을 때는 **측정일과 세션 수**를 함께 적는다.

사용: python -m evals.agent_metrics [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def sessions_dir(repo: Path) -> Path:
    return Path.home() / ".claude" / "projects" / str(repo).replace("/", "-")


def _ts(v) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_session(path: Path) -> dict | None:
    """세션 1개 → 집계 수치. 파싱 불가 줄은 조용히 건너뛴다(로그는 append-only 라 꼬리가 잘릴 수 있다)."""
    tools: Counter = Counter()
    n_user = n_assistant = 0
    tok_in = tok_out = tok_cache_read = 0
    stamps: list[datetime] = []

    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if t := _ts(rec.get("timestamp")):
                stamps.append(t)
            role = rec.get("type")
            msg = rec.get("message") or {}
            if role == "user":
                # 도구 결과도 user 로 기록된다 — 사람이 실제로 친 턴만 센다.
                content = msg.get("content")
                is_tool_result = isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                if not is_tool_result:
                    n_user += 1
            elif role == "assistant":
                n_assistant += 1
                u = msg.get("usage") or {}
                tok_in += int(u.get("input_tokens") or 0)
                tok_out += int(u.get("output_tokens") or 0)
                tok_cache_read += int(u.get("cache_read_input_tokens") or 0)
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        tools[b.get("name") or "?"] += 1

    if not stamps or n_assistant == 0:
        return None
    minutes = (max(stamps) - min(stamps)).total_seconds() / 60
    return {
        "session": path.stem[:8],
        "started": min(stamps).date().isoformat(),
        "minutes": round(minutes, 1),
        "human_turns": n_user,
        "assistant_turns": n_assistant,
        "tool_calls": sum(tools.values()),
        "tools": dict(tools),
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "tokens_cache_read": tok_cache_read,
    }


def aggregate(rows: list[dict]) -> dict:
    def med(key):
        return round(statistics.median(r[key] for r in rows), 1) if rows else 0

    def p90(key):
        vals = sorted(r[key] for r in rows)
        return round(vals[int(len(vals) * 0.9)], 1) if vals else 0

    tools: Counter = Counter()
    for r in rows:
        tools.update(r["tools"])
    return {
        "measured_at": datetime.now().date().isoformat(),
        "sessions": len(rows),
        "date_range": [min(r["started"] for r in rows), max(r["started"] for r in rows)] if rows else [],
        "per_session_median": {
            "minutes": med("minutes"),
            "tool_calls": med("tool_calls"),
            "human_turns": med("human_turns"),
            "assistant_turns": med("assistant_turns"),
            "tokens_out": med("tokens_out"),
        },
        "per_session_p90": {
            "tool_calls": p90("tool_calls"),
            "human_turns": p90("human_turns"),
            "tokens_out": p90("tokens_out"),
        },
        "totals": {
            "tool_calls": sum(r["tool_calls"] for r in rows),
            "human_turns": sum(r["human_turns"] for r in rows),
            "tokens_out": sum(r["tokens_out"] for r in rows),
            "tokens_cache_read": sum(r["tokens_cache_read"] for r in rows),
        },
        "tool_mix_top": tools.most_common(10),
        # ⚠️ 일부러 안 세는 것들 — 셀 수는 있지만 뜻이 없어서 뺐다(2026-08-07 확인):
        #  · 세션 시간 합계: `minutes` 는 첫/마지막 메시지의 벽시계 차라 열어둔 채 방치한 시간이
        #    통째로 들어간다. 실측 최댓값이 21,413분(14.9일)이었다 — 중앙값·p90 만 뜻이 있다.
        #  · 캐시 적중률: 로그의 `input_tokens` 는 캐시분을 빼고 적히므로 비율이 늘 99.99% 다
        #    (실측 32,220 vs 10.7억). 100% 짜리 지표는 개선을 못 보여준다.
        "not_measured": ["session_hours_total", "cache_hit_ratio"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, help="집계 결과를 이 경로에 저장")
    args = ap.parse_args()

    d = sessions_dir(REPO)
    if not d.exists():
        print(f"세션 로그 없음: {d}\n"
              f"이 레포에서 Claude Code 세션을 연 적이 있는 기계에서만 측정된다.")
        return 1

    rows = [r for r in (read_session(p) for p in sorted(d.glob("*.jsonl"))) if r]
    agg = aggregate(rows)

    print(f"# 에이전트 성과 측정 · {agg['measured_at']}")
    print(f"세션 {agg['sessions']}건 ({agg['date_range'][0]} → {agg['date_range'][1]})\n")
    m = agg["per_session_median"]
    p = agg["per_session_p90"]
    print("| 지표 | 세션당 중앙값 | p90 |")
    print("|---|---|---|")
    print(f"| 소요 시간 | {m['minutes']} min | (벽시계라 p90 무의미) |")
    print(f"| 도구 호출 | {m['tool_calls']} 회 | {p['tool_calls']} 회 |")
    print(f"| 사람 턴(지시·수정) | {m['human_turns']} 회 | {p['human_turns']} 회 |")
    print(f"| 에이전트 턴 | {m['assistant_turns']} 회 | — |")
    print(f"| 출력 토큰 | {m['tokens_out']:,.0f} | {p['tokens_out']:,.0f} |")
    t = agg["totals"]
    print(f"\n누적: 도구 호출 {t['tool_calls']:,}회 · 사람 턴 {t['human_turns']:,}회 · "
          f"출력 토큰 {t['tokens_out']:,} · 캐시 재사용 입력 {t['tokens_cache_read']:,}")
    print(f"안 세는 것(뜻이 없어서): {', '.join(agg['not_measured'])} — 이유는 소스 주석 참조")
    print("\n도구 사용 분포 (상위 10):")
    for name, n in agg["tool_mix_top"]:
        print(f"  {name:24} {n:6,}")

    if args.json:
        args.json.write_text(json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
