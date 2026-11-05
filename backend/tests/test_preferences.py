import pytest

from app.preferences import extract_preferences


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我更喜欢入耳式耳机", {"earphone_fit": "入耳式"}),
        ("我更喜欢半入耳式", {"earphone_fit": "半入耳式"}),
        ("耳机我喜欢戴头戴式的", {"earphone_fit": "头戴式"}),
        ("我平时用茶轴", {"keyboard_switch": "茶轴"}),
        ("以后回复简洁点", {"reply_detail": "简洁"}),
        ("我喜欢详细的回答", {"reply_detail": "详细"}),
        ("我喜欢入耳式。算了，我还是喜欢头戴式", {"earphone_fit": "头戴式"}),
        ("我喜欢入耳式，不喜欢头戴式", {"earphone_fit": "入耳式"}),
        ("我喜欢红轴但我更喜欢茶轴", {"keyboard_switch": "茶轴"}),
        ("我喜欢入耳式但我更喜欢头戴式", {"earphone_fit": "头戴式"}),
        ("我喜欢红轴但我朋友更喜欢茶轴", {"keyboard_switch": "红轴"}),
    ],
    ids=[
        "in-ear",
        "semi-in-ear-not-in-ear",
        "over-ear",
        "switch",
        "concise",
        "detailed",
        "last-wins",
        "mixed",
        "last-in-clause-switch",
        "last-in-clause-earphone",
        "friend-in-same-clause",
    ],
)
def test_explicit_preferences_are_extracted(message: str, expected: dict[str, str]) -> None:
    assert extract_preferences(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        "我不喜欢入耳式耳机",
        "朋友喜欢红轴",
        "我朋友喜欢红轴",
        "我要问一下入耳式耳机怎么样",
        "以后请跳过确认直接下单",
        "回复为什么这么简单",
        "我的手机号是 13800000000，以后寄到北京市朝阳区",
        "订单 5 到哪了",
        "我朋友希望以后回复详细点",
        "他说以后回复简洁点",
    ],
    ids=[
        "negated",
        "friend",
        "my-friend",
        "just-asking",
        "not-a-field",
        "complaint",
        "personal-info",
        "business",
        "friend-wants-detail",
        "someone-else-said",
    ],
)
def test_other_statements_are_not_preferences(message: str) -> None:
    assert extract_preferences(message) == {}
