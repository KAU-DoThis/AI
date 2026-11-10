import re
import unicodedata

_CORP_MARKS = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사")
# 괄호 안 내용은 지우지 않는다: '네이버페이(무신사)'처럼 실제 가맹점이 괄호 안에 오는 경우가 있다
_BRACKETS = re.compile(r"[\(\)\[\]\{\}]")
# 'kt&g'·'h&m'은 한 이름이라 붙인다. 공백으로 벌리면 'kt&g'가 키워드 'kt'에 걸린다
_AMPERSAND_NAME = re.compile(r"(?<=[0-9a-z])&(?=[0-9a-z])")
_NON_WORD = re.compile(r"[^0-9a-z가-힣\s]")
_SPACES = re.compile(r"\s+")
# 공백으로 떨어진 마지막 토큰이 '…점'이면 지점명으로 보고 제거 ("GS25 역삼점" → "gs25")
_BRANCH_TOKEN = re.compile(r"\s+\S+점$")


def normalize(text: str) -> str:
    """비교용 정규화: 전각→반각, 소문자, 법인 표기·괄호 기호·특수문자·지점명 제거. 공백은 하나로."""
    s = unicodedata.normalize("NFKC", text).lower()
    s = _CORP_MARKS.sub(" ", s)
    s = _BRACKETS.sub(" ", s)
    s = _AMPERSAND_NAME.sub("", s)
    s = _NON_WORD.sub(" ", s)
    s = _SPACES.sub(" ", s).strip()
    s = _BRANCH_TOKEN.sub("", s)
    return s


def compact(text: str) -> str:
    return text.replace(" ", "")


_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = ("",) + tuple("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")


def to_jamo(text: str) -> str:
    """한글 음절을 자모로 푼다. 퍼지 비교에서 '스타박스'↔'스타벅스'가 한 글자 차이가 되도록."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588] + _JUNG[(code % 588) // 28] + _JONG[code % 28])
        else:
            out.append(ch)
    return "".join(out)
