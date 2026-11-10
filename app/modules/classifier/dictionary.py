"""2단: 가맹점 사전 매칭 + 퍼지 매칭."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rapidfuzz import fuzz, process

from app.modules.classifier.categories import BY_CODE
from app.modules.classifier.normalize import compact, normalize, to_jamo

DEFAULT_PATH = Path(__file__).parent / "data" / "merchants.csv"
# 짧은 영문 키워드(cu, kt, skt …)는 다른 단어 안에서 잘못 걸리기 쉬워 토큰 경계로만 매칭
_SHORT_ASCII_MAX = 4
# 퍼지는 너무 짧은 키워드에 쓰면 오탐이 많다
_FUZZY_MIN_KEYWORD_LEN = 3

# contains: 적요 어디든 포함되면 매칭 (브랜드명)
# suffix:   단어 끝에 올 때만 매칭 (업종 접미어: '온누리약국'은 O, '국회의원후원회'는 X)
MatchType = Literal["contains", "suffix"]


@dataclass(frozen=True)
class Entry:
    keyword: str  # 정규화된 키워드
    category_code: str
    match: MatchType


@dataclass(frozen=True)
class Match:
    keyword: str
    category_code: str
    score: float  # 0~100


def _is_short_ascii(keyword: str) -> bool:
    return keyword.isascii() and len(compact(keyword)) <= _SHORT_ASCII_MAX


class MerchantDictionary:
    def __init__(self, entries: list[Entry]):
        unknown = {e.category_code for e in entries if e.category_code not in BY_CODE}
        if unknown:
            raise ValueError(f"카테고리 목록에 없는 코드: {sorted(unknown)}")
        seen: set[str] = set()
        dupes = {e.keyword for e in entries if e.keyword in seen or seen.add(e.keyword)}
        if dupes:
            raise ValueError(f"사전에 중복 키워드: {sorted(dupes)}")

        # 긴 키워드부터 비교해야 '이마트24'가 '이마트'보다 먼저 걸린다
        self._entries = sorted(entries, key=lambda e: len(compact(e.keyword)), reverse=True)
        self._patterns = {e.keyword: self._pattern(e) for e in self._entries}
        # 퍼지는 브랜드명만, 자모로 풀어서 비교: 자모 문자열 → 엔트리
        self._fuzzy = {
            to_jamo(compact(e.keyword)): e
            for e in self._entries
            if e.match == "contains" and len(compact(e.keyword)) >= _FUZZY_MIN_KEYWORD_LEN
        }

    def brand_keywords(self) -> frozenset[str]:
        """브랜드 단어(contains)만. 업종 단어(suffix: 학원·헬스장)는 서로 다른 가게라 묶으면 안 된다."""
        return frozenset(e.keyword for e in self._entries if e.match == "contains")

    @staticmethod
    def _pattern(e: Entry) -> re.Pattern | None:
        """substring 비교로 충분하면 None, 경계가 필요하면 정규식."""
        kw = re.escape(e.keyword)
        if e.match == "suffix":
            return re.compile(rf"{kw}(?=\s|$)")
        if _is_short_ascii(e.keyword):
            return re.compile(rf"(?<![0-9a-z]){kw}(?![0-9a-z])")
        return None

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "MerchantDictionary":
        with path.open(encoding="utf-8") as f:
            entries = [
                Entry(
                    keyword=normalize(row["keyword"]),
                    category_code=row["category_code"].strip(),
                    match=(row.get("match") or "contains").strip(),
                )
                for row in csv.DictReader(f)
            ]
        return cls(entries)

    def exact(self, text: str) -> Match | None:
        """정규화한 적요 안에 사전 키워드가 있으면 매칭."""
        c = compact(text)
        for e in self._entries:
            pattern = self._patterns[e.keyword]
            hit = pattern.search(text) if pattern else compact(e.keyword) in c
            if hit:
                return Match(e.keyword, e.category_code, 100.0)
        return None

    def fuzzy(self, text: str, min_score: int) -> Match | None:
        """오타·변형 표기 ('스타박스', '배달의민죡' 등)."""
        c = compact(text)
        if len(c) < _FUZZY_MIN_KEYWORD_LEN:
            return None
        found = process.extractOne(to_jamo(c), self._fuzzy.keys(), scorer=fuzz.ratio, score_cutoff=min_score)
        if found is None:
            return None
        jamo_key, score, _ = found
        e = self._fuzzy[jamo_key]
        return Match(e.keyword, e.category_code, float(score))
