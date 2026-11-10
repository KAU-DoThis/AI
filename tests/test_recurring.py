from datetime import date

from app.modules.analysis.recurring import find_recurring
from app.modules.classifier.dictionary import MerchantDictionary
from app.schemas.classify import TransactionIn

_ids = iter(range(1, 10_000))


def tx(when: str, amount: int, payee: str | None, account: str = "BANK", **kw) -> TransactionIn:
    return TransactionIn(transaction_id=next(_ids), occurred_at=f"{when}T10:00:00+09:00", amount=amount,
                         direction=kw.pop("direction", "OUT"), merchant_or_payee=payee, account_type=account, **kw)


BRANDS = MerchantDictionary.load().brand_keywords()


def run(classifier, txs):
    return find_recurring(txs, [classifier.classify(t) for t in txs], BRANDS)


def test_two_academies_are_separate(classifier):
    # '학원'은 업종 단어라 묶으면 안 된다: 묶으면 한 달 두 번이 되어 둘 다 놓친다
    txs = [tx(f"2026-{m:02d}-05", 200000, "한빛수학학원", "CARD") for m in (8, 9, 10)] + [
        tx(f"2026-{m:02d}-07", 180000, "다솜영어학원", "CARD") for m in (8, 9, 10)]
    assert sorted(c.name for c in run(classifier, txs)) == ["다솜영어학원", "한빛수학학원"]


def test_monthly_rent_found(classifier):
    txs = [tx("2026-08-25", 600000, "김*수"), tx("2026-09-24", 600000, "김*수"), tx("2026-10-26", 600000, "김*수")]
    [c] = run(classifier, txs)
    assert c.rule.pay_day == 25 and c.rule.pay_amount == 600000
    # 금액이 매달 같아도 ±10% 범위로 준다 (API 명세 v4·시안 04: 600,000 → 540,000~660,000)
    assert c.rule.match_condition.amount_mode == "RANGE"
    assert (c.rule.match_condition.min_amount, c.rule.match_condition.max_amount) == (540000, 660000)
    assert c.occurrence_count == 3 and c.proposal_key == "fixed-1"
    assert c.next_expected_date == date(2026, 11, 25)
    assert c.reason == "지난 3개월 매월 25일 전후 김*수님께 60만 원을 보냈어요"
    # 집주인 개인 계좌 이체는 이름만 찍힌다 → 패턴으로 월세 (시안 04 블록 이름 '월세')
    assert c.name == "월세" and c.category_code == "HOUSING"
    body = c.model_dump(mode="json")
    assert body["rule"]["matchCondition"]["dayWindow"] == 3 and body["foundFrom"] == "TRANSACTIONS"


def test_varying_telecom_uses_range(classifier):
    txs = [tx("2026-08-15", 55000, "SKT", "CARD"), tx("2026-09-15", 57200, "SKT", "CARD"),
           tx("2026-10-16", 54100, "SKT", "CARD")]
    [c] = run(classifier, txs)
    assert c.category_code == "TELECOM" and c.name == "통신비"
    assert c.rule.match_condition.amount_mode == "RANGE"
    assert (c.rule.match_condition.min_amount, c.rule.match_condition.max_amount) == (48700, 59500)


def test_frequent_same_price_coffee_is_not_fixed(classifier):
    txs = [tx(f"2026-{m:02d}-{d:02d}", 4500, "스타벅스 강남점", "CARD") for m in (8, 9, 10) for d in (3, 5, 20)]
    assert run(classifier, txs) == []


def test_needs_three_consecutive_months(classifier):
    two = [tx("2026-09-10", 13500, "넷플릭스", "CARD"), tx("2026-10-10", 13500, "넷플릭스", "CARD")]
    gap = [tx("2026-06-10", 13500, "넷플릭스", "CARD"), tx("2026-08-10", 13500, "넷플릭스", "CARD"),
           tx("2026-10-10", 13500, "넷플릭스", "CARD")]
    assert run(classifier, two) == [] and run(classifier, gap) == []


def test_day_and_amount_limits(classifier):
    far_day = [tx("2026-08-05", 30000, "헬스장A", "CARD"), tx("2026-09-05", 30000, "헬스장A", "CARD"),
               tx("2026-10-12", 30000, "헬스장A", "CARD")]
    big_jump = [tx("2026-08-05", 30000, "헬스장B", "CARD"), tx("2026-09-05", 30000, "헬스장B", "CARD"),
                tx("2026-10-05", 40000, "헬스장B", "CARD")]
    assert run(classifier, far_day) == [] and run(classifier, big_jump) == []


def test_month_end_wraps_to_next_month(classifier):
    txs = [tx("2026-08-31", 9900, "유튜브프리미엄", "CARD"), tx("2026-10-01", 9900, "유튜브프리미엄", "CARD"),
           tx("2026-10-30", 9900, "유튜브프리미엄", "CARD")]
    # 9월 결제가 10/1로 밀림 → 달이 8·10·10이 되어 한 달 두 번 = 고정지출 아님 (보수적으로 안 잡음)
    assert run(classifier, txs) == []
    ok = [tx("2026-08-31", 9900, "왓챠", "CARD"), tx("2026-09-30", 9900, "왓챠", "CARD"),
          tx("2026-10-31", 9900, "왓챠", "CARD")]
    [c] = run(classifier, ok)
    assert c.rule.pay_day == 31 and c.next_expected_date == date(2026, 11, 30)


def test_excluded_and_canceled_are_ignored(classifier):
    card_bill = [tx(f"2026-{m:02d}-14", 300000, "KB국민카드") for m in (8, 9, 10)]
    own = [tx(f"2026-{m:02d}-01", 200000, "내 적금통장", is_own_account_transfer=True) for m in (8, 9, 10)]
    canceled = [tx(f"2026-{m:02d}-10", 13500, "티빙", "CARD", is_canceled=True) for m in (8, 9, 10)]
    income = [tx(f"2026-{m:02d}-25", 500000, "부모님", direction="IN") for m in (8, 9, 10)]
    assert run(classifier, card_bill + own + canceled + income) == []


def test_odd_amount_in_group_is_ignored(classifier):
    txs = [tx(f"2026-{m:02d}-10", 13500, "넷플릭스", "CARD") for m in (8, 9, 10)] + [
        tx("2026-09-21", 2500, "넷플릭스", "CARD")]  # 다른 금액 결제 한 건은 무시
    [c] = run(classifier, txs)
    assert c.occurrence_count == 3 and len(c.transaction_ids) == 3


def test_same_brand_different_spelling_grouped(classifier):
    txs = [tx("2026-08-03", 13500, "NETFLIX.COM", "CARD"), tx("2026-09-03", 13500, "NETFLIX", "CARD"),
           tx("2026-10-03", 13500, "Netflix.com", "CARD")]
    [c] = run(classifier, txs)
    assert c.category_code == "SUBSCRIPTION" and c.occurrence_count == 3 and c.name == "Netflix.com"
    # 한계: 한글 '넷플릭스'와 영문 'netflix'는 사전 단어가 달라 다른 묶음이 된다


def test_insurer_paid_by_bank_is_not_called_person(classifier):
    txs = [tx(f"2026-{m:02d}-20", 23000, "KB손해보험") for m in (8, 9, 10)]
    [c] = run(classifier, txs)
    assert "님께" not in c.reason


def test_sorted_by_amount(classifier):
    txs = [tx(f"2026-{m:02d}-10", 13500, "넷플릭스", "CARD") for m in (8, 9, 10)] + [
        tx(f"2026-{m:02d}-25", 600000, "김*수") for m in (8, 9, 10)]
    assert [c.balance for c in run(classifier, txs)] == [600000, 13500]
    assert [c.proposal_key for c in run(classifier, txs)] == ["fixed-1", "fixed-2"]


def test_small_monthly_transfer_to_person_is_not_rent(classifier):
    # 매달 같은 사람에게 보내도 20만 원 아래면 월세로 보지 않는다 (모임 회비 등)
    txs = [tx(f"2026-{m:02d}-05", 30000, "박OO") for m in (8, 9, 10)]
    [c] = run(classifier, txs)
    assert c.name == "박OO" and c.category_code is None


def test_company_payee_is_not_renamed_rent(classifier):
    # 회사 이름(사람 아님)으로 나간 큰 송금은 '월세'로 이름 붙이지 않는다
    txs = [tx(f"2026-{m:02d}-20", 300000, "대한상사") for m in (8, 9, 10)]
    [c] = run(classifier, txs)
    assert c.name != "월세"


def test_each_subscription_is_its_own_block(classifier):
    # FE 기준(11/10): 고정지출은 금액·받는 곳·날짜 조건이 달라 서비스마다 블록 하나. 구독도 묶지 않는다
    txs = [tx(f"2026-{m:02d}-03", 7000, "NETFLIX.COM", "CARD") for m in (8, 9, 10)] + [
        tx(f"2026-{m:02d}-12", 10900, "멜론", "CARD") for m in (8, 9, 10)]
    found = run(classifier, txs)
    assert sorted((c.name, c.rule.pay_day, c.rule.pay_amount) for c in found) == [("NETFLIX.COM", 3, 7000), ("멜론", 12, 10900)]
