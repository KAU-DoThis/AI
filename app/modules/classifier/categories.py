"""카테고리 목록 — 기능명세서 F-20(거래 분류): 소비 12개 + 이체 유형 4개.

이름·구분(EXPENSE/TRANSFER)은 명세서와 BE `categories`(code, name, category_type, display_order) 기준.
code 값은 BE `db/schema.sql` 카테고리 시드 기준(10/24). 바뀌면 여기와 merchants.csv만 맞춘다.
"""

from dataclasses import dataclass
from typing import Literal

CategoryType = Literal["EXPENSE", "TRANSFER"]


@dataclass(frozen=True)
class Category:
    code: str
    name: str
    category_type: CategoryType
    display_order: int


_EXPENSE = (
    ("FOOD", "식비"),
    ("DELIVERY_DINING", "배달/외식"),
    ("CAFE", "카페/간식"),
    ("TRANSPORT", "교통"),
    ("HOUSING", "주거/관리비"),
    ("TELECOM", "통신"),
    ("LIVING", "생활/생필품"),
    ("SHOPPING", "쇼핑/의류"),
    ("LEISURE", "문화/여가"),
    ("HEALTH", "의료/건강"),
    ("SUBSCRIPTION", "구독료"),
    ("ETC", "기타"),
)
# 이체 유형은 사용자 답(F-15)으로 정해진다. AI가 직접 붙이는 건 SELF_TRANSFER뿐
_TRANSFER = (
    ("DUTCH_PAY", "더치페이"),
    ("GIFT", "선물/경조사"),
    ("LOAN_OUT", "빌려준 돈"),
    ("SELF_TRANSFER", "자기 계좌 간 이동"),
)

CATEGORIES: tuple[Category, ...] = tuple(
    Category(code, name, "EXPENSE", i) for i, (code, name) in enumerate(_EXPENSE, 1)
) + tuple(
    Category(code, name, "TRANSFER", i) for i, (code, name) in enumerate(_TRANSFER, len(_EXPENSE) + 1)
)

BY_CODE: dict[str, Category] = {c.code: c for c in CATEGORIES}
