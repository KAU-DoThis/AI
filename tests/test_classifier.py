from datetime import datetime

import pytest

from app.modules.classifier.dictionary import MerchantDictionary
from app.modules.classifier.normalize import normalize
from app.modules.classifier.pipeline import Classifier
from app.schemas.classify import TransactionIn


def tx(merchant: str | None, direction: str = "OUT", **kw) -> TransactionIn:
    return TransactionIn(
        transaction_id=kw.pop("transaction_id", 1),
        occurred_at=kw.pop("occurred_at", datetime(2026, 10, 10, 12, 0)),
        amount=kw.pop("amount", 10000),
        direction=direction,
        merchant_or_payee=merchant,
        **kw,
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("스타벅스(강남R점)", "스타벅스"),
        ("네이버페이(무신사)", "네이버페이 무신사"),
        ("(주)우아한형제들", "우아한형제들"),
        ("GS25 역삼점", "gs25"),
        ("ＣＧＶ 용산", "cgv 용산"),
        ("KT&G 상상마당", "ktg 상상마당"),
        ("H&M", "hm"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "description, code",
    [
        ("스타벅스 강남R점", "CAFE"),
        ("스타벅스강남R점", "CAFE"),
        ("배달의민족", "DELIVERY_DINING"),
        ("(주)우아한형제들", "DELIVERY_DINING"),
        ("쿠팡이츠", "DELIVERY_DINING"),
        ("GS25 역삼점", "FOOD"),
        ("이마트24 선릉점", "FOOD"),
        ("이마트 성수점", "FOOD"),
        ("NETFLIX.COM", "SUBSCRIPTION"),
        ("KT 통신요금", "TELECOM"),
        ("코레일 KTX", "TRANSPORT"),
        ("서울교통공사", "TRANSPORT"),
        # 11/01 상가정보 점검: '교통공사'가 먼저 걸려 교통으로 갔음. 같은 길이면 csv 앞줄이 이김 → 구내식당을 앞에
        ("서울교통공사본사구내식당", "DELIVERY_DINING"),
        ("BHC 한양대점", "DELIVERY_DINING"),
        ("비에이치씨한양대", "DELIVERY_DINING"),
        ("더벤티스타필드운정점", "CAFE"),
    ],
)
def test_dictionary(classifier, description, code):
    r = classifier.classify(tx(description))
    assert r.category_code == code
    assert r.classify_stage == 1
    assert r.excluded is False
    assert r.needs_question is False


def test_short_ascii_keyword_needs_token_boundary(classifier):
    # 'cu'가 다른 영단어 안에서 걸리면 안 된다
    r = classifier.classify(tx("CULTURELAND"))
    assert r.category_code != "FOOD"


def test_fuzzy_typo(classifier):
    r = classifier.classify(tx("배달의민죡"))
    assert r.category_code == "DELIVERY_DINING"
    assert r.classify_stage == 2


def test_short_typo_below_spec_threshold(classifier):
    # 명세서 퍼지 기준 90: '스타박스'(자모 88.9점)는 퍼지에서 못 잡고 다음 단계로 넘어간다.
    # 상가정보에 실제 '스타박스'(중고 상품 소매)가 있어 사전에도 넣지 않는다
    assert classifier.classify(tx("스타박스")).classify_stage is None


@pytest.mark.parametrize("name", ["BATMAN STORE", "WHATMORE"])
def test_atm_inside_word_is_not_withdrawal(classifier, name):
    assert classifier.classify(tx(name)).excluded is False


@pytest.mark.parametrize("name", ["ATM 출금", "타행ATM", "CD출금", "현금인출"])
def test_cash_withdrawal_is_excluded(classifier, name):
    assert classifier.classify(tx(name, account_type="BANK")).excluded is True


@pytest.mark.parametrize("name", ["현대카드 연회비", "삼성카드 포인트몰"])
def test_card_company_on_card_approval_is_spending(classifier, name):
    r = classifier.classify(tx(name, account_type="CARD"))
    assert r.excluded is False
    assert r.needs_question is True


def test_card_company_on_bank_is_card_payment(classifier):
    assert classifier.classify(tx("삼성카드", account_type="BANK")).excluded is True


@pytest.mark.parametrize("name", ["신한은행 적금", "주택청약", "토스증권", "한국장학재단", "학자금 상환"])
def test_saving_from_bank_is_not_spending(classifier, name):
    r = classifier.classify(tx(name, account_type="BANK", amount=300000))
    assert r.excluded is True
    assert r.needs_question is False


def test_negative_amount_is_excluded(classifier):
    assert classifier.classify(tx("스타벅스", amount=-5000)).excluded is True


@pytest.mark.parametrize(
    "name, code",
    [("베이프샵 화전점", "LIVING"), ("OO전담", "LIVING"), ("VAPE STORE", "LIVING"), ("무인아이스크림", "CAFE"),
     ("아이스크림 할인점", "CAFE"), ("피시방", "LEISURE"), ("에쓰오일", "TRANSPORT"), ("MUSINSA", "SHOPPING"),
     ("MCDONALD'S", "DELIVERY_DINING")],
)
def test_everyday_spending(classifier, name, code):
    assert classifier.classify(tx(name)).category_code == code


@pytest.mark.parametrize("name", ["다날", "KG모빌리언스", "SKT 소액결제", "KT 소액결제", "LGU+ 휴대폰결제", "휴대폰결제"])
def test_micropayment_asks_what_was_bought(classifier, name):
    r = classifier.classify(tx(name))
    assert (r.category_code, r.question_type) == (None, "UNKNOWN_ITEM")


def test_himart_is_not_emart(classifier):
    assert classifier.classify(tx("롯데하이마트")).category_code == "SHOPPING"


@pytest.mark.parametrize("name", ["KT&G 상상마당", "kt&g"])
def test_ktng_is_not_telecom(classifier, name):
    assert classifier.classify(tx(name)).category_code != "TELECOM"


@pytest.mark.parametrize("name", ["KT", "KT 통신요금", "kt 인터넷"])
def test_kt_is_still_telecom(classifier, name):
    assert classifier.classify(tx(name)).category_code == "TELECOM"


@pytest.mark.parametrize("name", [None, "", "   ", "-"])
def test_nameless_card_asks_without_jeff(name):
    calls = []

    class FakeJeff:
        def route(self, t):
            calls.append(t)
            return "slm"

    c = Classifier(MerchantDictionary.load(), question_threshold=0.7, fuzzy_min_score=90, jeff=FakeJeff())
    r = c.classify(tx(name, account_type="CARD"))
    assert r.category_code is None
    assert r.question_type == "LOW_CONFIDENCE"
    assert r.status == "NEEDS_REVIEW"
    assert calls == []


@pytest.mark.parametrize(
    "amount, code, question",
    [(50000, None, "PERSONAL_TRANSFER"), (9000, "ETC", None)],
)
def test_nameless_bank_follows_transfer_rule(classifier, amount, code, question):
    r = classifier.classify(tx(None, account_type="BANK", amount=amount))
    assert (r.category_code, r.question_type) == (code, question)


@pytest.mark.parametrize("direction, kw", [("IN", {}), ("OUT", {"is_own_account_transfer": True})])
def test_nameless_non_spending_is_excluded(classifier, direction, kw):
    assert classifier.classify(tx(None, direction=direction, **kw)).excluded is True


def test_card_payment_is_not_spending(classifier):
    r = classifier.classify(tx("신한카드", amount=850000))
    assert r.category_code is None
    assert r.excluded is True
    assert r.matched_keyword == "신한카드"


def test_check_card_is_not_card_payment(classifier):
    r = classifier.classify(tx("신한체크 스타벅스"))
    assert r.category_code == "CAFE"


def test_self_transfer(classifier):
    r = classifier.classify(tx("홍길동", is_own_account_transfer=True))
    assert r.category_code == "SELF_TRANSFER"
    assert r.excluded is True


def test_income(classifier):
    r = classifier.classify(tx("급여", direction="IN"))
    assert r.category_code is None
    assert r.excluded is True


def test_pg_only_asks_question(classifier):
    r = classifier.classify(tx("네이버페이"))
    assert r.needs_question is True
    assert r.question_type == "UNKNOWN_ITEM"


def test_pg_with_merchant_uses_merchant(classifier):
    r = classifier.classify(tx("네이버페이 무신사"))
    assert r.category_code == "SHOPPING"


def test_bank_name_is_not_card_payment(classifier):
    # 은행 출금이 카드대금으로 빠지면 소비가 통째로 사라진다
    assert classifier.classify(tx("KB국민은행")).matched_keyword is None


@pytest.mark.parametrize(
    "description, code",
    [("네이버페이(무신사)", "SHOPPING"), ("카카오페이(스타벅스)", "CAFE")],
)
def test_merchant_inside_brackets(classifier, description, code):
    assert classifier.classify(tx(description)).category_code == code


@pytest.mark.parametrize(
    "description, code",
    [("온누리약국", "HEALTH"), ("준오헤어 강남점", "LIVING"), ("콘텐츠웨이브", "SUBSCRIPTION")],
)
def test_suffix_keywords_match_at_word_end(classifier, description, code):
    assert classifier.classify(tx(description)).category_code == code


@pytest.mark.parametrize(
    "description, code",
    [("명동칼국수", "DELIVERY_DINING"), ("주주헤어샵", "LIVING"), ("진원철물", "LIVING"), ("복성여행사", "LEISURE"),
     ("동네주유소", "TRANSPORT"), ("하니커피", "CAFE"),
     # 끝말이 '카페'여도 공부·놀이 공간은 카페가 아니다 (긴 말이 먼저 걸린다)
     ("봄빛스터디카페", "LEISURE"), ("나비랑키즈카페", "LEISURE")],
)
def test_business_word_at_end_goes_to_dictionary(classifier, description, code):
    r = classifier.classify(tx(description))
    assert r.category_code == code and r.classify_stage == 1


@pytest.mark.parametrize("description", ["국회의원후원회", "웨이브파크", "커피인더스터디홀", "장승정보통신", "은혜수산"])
def test_suffix_keywords_do_not_match_inside_words(classifier, description):
    r = classifier.classify(tx(description))
    assert r.classify_stage != 1


def test_longer_keyword_wins(classifier):
    assert classifier.classify(tx("멜론티켓")).category_code == "LEISURE"


def test_canceled_keeps_category_but_not_spending(classifier):
    # 취소는 BE가 is_canceled로 처리한다. AI는 카테고리만 그대로 붙인다
    r = classifier.classify(tx("배달의민족", is_canceled=True))
    assert r.category_code == "DELIVERY_DINING"
    assert r.excluded is False


def test_unknown_merchant_asks_question(classifier):
    r = classifier.classify(tx("홍길동"))
    assert r.category_code is None
    assert r.classify_stage is None
    assert r.question_type == "LOW_CONFIDENCE"


@pytest.mark.parametrize(
    "description, code",
    [
        # 카드 명세서에 실제로 찍히는 한글 표기·법인명
        ("지에스25 화정점", "FOOD"),
        ("코리아세븐", "FOOD"),
        ("비지에프리테일", "FOOD"),
        ("에스씨케이컴퍼니", "CAFE"),
        ("서울교통공사", "TRANSPORT"),
        ("경기버스", "TRANSPORT"),
        ("항공대 학생식당", "DELIVERY_DINING"),
    ],
)
def test_statement_spellings(classifier, description, code):
    assert classifier.classify(tx(description)).category_code == code


def test_suffix_bus_not_inside_word(classifier):
    # '버스'는 단어 끝일 때만: '버스킹카페'가 교통으로 가면 안 된다
    assert classifier.classify(tx("버스킹카페")).category_code != "TRANSPORT"


@pytest.mark.parametrize("merchant", ["쿠팡", "11번가", "G마켓", "SSG닷컴"])
def test_open_market_always_asks(classifier, merchant):
    # 기능명세서 F-20: 오픈마켓은 품목을 몰라 항상 묻는다
    r = classifier.classify(tx(merchant))
    assert r.category_code is None
    assert r.question_type == "UNKNOWN_ITEM"


def test_personal_transfer_over_10000_asks(classifier):
    r = classifier.classify(tx("홍길동", account_type="BANK", amount=30000))
    assert r.category_code is None
    assert r.question_type == "PERSONAL_TRANSFER"


def test_personal_transfer_under_10000_is_etc_without_question(classifier):
    r = classifier.classify(tx("홍길동", account_type="BANK", amount=5000))
    assert r.category_code == "ETC"
    assert r.needs_question is False


def test_bank_debit_in_dictionary_is_not_transfer(classifier):
    # 은행 자동이체라도 사전에 있으면(통신요금 등) 송금으로 묻지 않는다
    r = classifier.classify(tx("KT 통신요금", account_type="BANK", amount=55000))
    assert r.category_code == "TELECOM"
    assert r.needs_question is False


def test_dictionary_codes_are_expense_categories():
    from app.modules.classifier.categories import BY_CODE
    from app.modules.classifier.dictionary import MerchantDictionary

    d = MerchantDictionary.load()
    assert {BY_CODE[e.category_code].category_type for e in d._entries} == {"EXPENSE"}


def test_category_codes_match_be_seed():
    # BE db/schema.sql 카테고리 시드 (10/24)
    from app.modules.classifier.categories import BY_CODE

    assert set(BY_CODE) == {
        "FOOD", "DELIVERY_DINING", "CAFE", "TRANSPORT", "HOUSING", "TELECOM", "LIVING", "SHOPPING",
        "LEISURE", "HEALTH", "SUBSCRIPTION", "ETC", "DUTCH_PAY", "GIFT", "LOAN_OUT", "SELF_TRANSFER",
    }


def test_status_confirmed_when_classified(classifier):
    r = classifier.classify(tx("스타벅스 강남점"))
    assert r.status == "CONFIRMED"
    assert r.classified_by == "AUTO"
    assert r.question_options == []


def test_easy_pay_needs_review_with_figma_options(classifier):
    r = classifier.classify(tx("네이버페이"))
    assert r.status == "NEEDS_REVIEW"
    assert r.question_options == ["LIVING", "FOOD", "SHOPPING", "ETC", "DUTCH_PAY"]


def test_personal_transfer_options_are_transfer_types(classifier):
    from app.modules.classifier.categories import BY_CODE

    r = classifier.classify(tx("홍길동", account_type="BANK", amount=30000))
    assert r.status == "NEEDS_REVIEW"
    assert {BY_CODE[c].category_type for c in r.question_options if c != "ETC"} == {"TRANSFER"}


@pytest.mark.parametrize("hour", [22, 23, 0, 4])
def test_late_night_delivery_asks_context_but_keeps_category(classifier, hour):
    r = classifier.classify(tx("배달의민족", occurred_at=datetime(2026, 10, 10, hour, 30)))
    assert r.category_code == "DELIVERY_DINING"
    assert r.status == "CONFIRMED"
    assert r.question_type == "LATE_NIGHT_FOOD"
    assert r.question_options == ["야근", "약속", "그냥"]


@pytest.mark.parametrize("hour", [5, 12, 21])
def test_daytime_delivery_no_question(classifier, hour):
    r = classifier.classify(tx("배달의민족", occurred_at=datetime(2026, 10, 10, hour, 30)))
    assert r.needs_question is False


def test_late_night_only_for_delivery_dining(classifier):
    r = classifier.classify(tx("스타벅스 강남점", occurred_at=datetime(2026, 10, 10, 23, 0)))
    assert r.needs_question is False


def test_options_never_exceed_max(classifier):
    for r in (classifier.classify(tx(m, account_type="BANK", amount=30000)) for m in ["네이버페이", "홍길동", "알수없는가게"]):
        assert len(r.question_options) <= 5


def test_low_confidence_options_top3_plus_etc(classifier):
    # F-15: 신뢰도 0.7 미만이면 상위 후보 3개 / 기타
    r = classifier.classify(tx("알수없는가게"))
    assert r.question_type == "LOW_CONFIDENCE"
    assert len(r.question_options) == 4 and r.question_options[-1] == "ETC"


@pytest.mark.parametrize("description", ["KB국민 ATM출금", "신한 현금인출", "우리 CD출금"])
def test_cash_withdrawal_excluded_without_question(classifier, description):
    r = classifier.classify(tx(description, account_type="BANK", amount=50000))
    assert r.excluded is True
    assert r.category_code is None
    assert r.needs_question is False


@pytest.mark.parametrize(
    "description, code",
    [
        # 10/25 학습: Jeff가 영문 브랜드를 못 배움(APPLE → ask) → 아는 브랜드는 사전이 잡는다 (26 노트)
        ("APPLE.COM/BILL", "SUBSCRIPTION"),
        ("GOOGLE*PLAY", "SUBSCRIPTION"),
        ("MICROSOFT*365", "SUBSCRIPTION"),
        ("ZOOM.US", "SUBSCRIPTION"),
        ("AMAZON WEB SERVICES", "SUBSCRIPTION"),
        ("STEAMGAMES.COM", "LEISURE"),
        ("NINTENDO ESHOP", "LEISURE"),
        ("XBOX", "LEISURE"),
        ("UBER EATS", "DELIVERY_DINING"),
        ("UBER TRIP", "TRANSPORT"),
        ("AGODA HOTEL", "LEISURE"),
        ("BOOKING.COM", "LEISURE"),
        ("ASIANA AIRLINES", "LEISURE"),
        ("UDEMY", "ETC"),
        ("IKEA 고양", "LIVING"),
    ],
)
def test_english_brands_in_dictionary(classifier, description, code):
    r = classifier.classify(tx(description))
    assert (r.category_code, r.classify_stage) == (code, 1)


@pytest.mark.parametrize("description", ["AMAZON MARKETPLACE", "PAYPAL *PURCHASE", "XBOXER", "SEOUL PIZZA"])
def test_english_non_brands_not_caught(classifier, description):
    # 오픈마켓·결제대행은 품목을 모르고, 처음 보는 가게는 Jeff까지 내려가야 한다
    assert classifier.classify(tx(description)).category_code is None
