"""The native (Rust) feed ranker and the signals Python feeds into it.

Rust has its own unit tests (`cargo test`); these tests pin the behaviour as
seen from Python, through the real extension module.
"""

import itertools
import time
from typing import Any

import pytest
import telegram_me_native as native

from telegram_me.services.feed_signals import (
    candidate,
    media_counts,
    parse_compact_number,
    reaction_totals,
)

NOW = 1_800_000_000
HOUR = 3600
STORY = (
    "Министерство обороны сообщило о новых поставках техники в регион, поставки "
    "начнутся уже на следующей неделе и продлятся до конца месяца по плану"
)


WEATHER = "Погода на выходные: солнечно, без осадков, до двадцати градусов тепла днём"
ROCKET_1 = "Запуск ракеты с космодрома перенесли, старт ракеты состоится завтра утром"
ROCKET_2 = "Старт ракеты с космодрома: запуск ракеты назначен на утро"
FOOTBALL = "Футбольный матч сборной завершился победой, гол забит на последней минуте"


def make(
    channel: str, post_id: int, hours_ago: float = 1, views: int = 1000, **extra: Any
) -> native.Candidate:
    return native.Candidate(
        channel=channel,
        post_id=post_id,
        published_at=int(NOW - hours_ago * HOUR),
        views=views,
        subscribers=extra.pop("subscribers", 10_000),
        **extra,
    )


def options(**overrides: Any) -> native.RankOptions:
    return native.RankOptions(now=NOW, **overrides)


# --- signals -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("15", 15),
        ("1.2K", 1200),
        ("3.5M", 3_500_000),
        ("15 400", 15400),
        ("10\N{NO-BREAK SPACE}281", 10281),
        ("n/a", 0),
        ("", 0),
        (None, 0),
    ],
)
def test_parse_compact_number(value: str | None, expected: int) -> None:
    assert parse_compact_number(value) == expected


def test_reaction_totals() -> None:
    reacts: list[dict[str, Any]] = [
        {"type": "emoji", "emoji": "👍", "count": "1.2K"},
        {"type": "emoji", "emoji": "🔥", "count": "300"},
        {"type": "telegram_stars", "emoji": "⭐", "count": "50"},
        {"type": "emoji", "emoji": "👎", "count": "20"},
        {"type": "custom_emoji", "emoji": None, "emoji_id": "1", "count": "999"},
    ]
    assert reaction_totals(reacts) == (1550, 20)
    assert reaction_totals(None) == (0, 0)


def test_media_counts() -> None:
    content = {
        "media": [
            {"type": "image"},
            {"type": "image"},
            {"type": "video"},
            {"type": "roundvideo"},
            {"type": "gif"},
            {"type": "sticker"},
            {"type": "voice"},
        ],
        "poll": {"question": "?"},
    }
    assert media_counts(content) == {"photos": 2, "videos": 2, "gifs": 2, "polls": 1}
    assert media_counts({}) == {"photos": 0, "videos": 0, "gifs": 0, "polls": 0}


def test_candidate_from_raw_post() -> None:
    post = {
        "id": 42,
        "content": {"text": {"string": "hello", "html": "hello"}},
        "footer": {"views": "1.5K", "edited": True, "date": {"string": "x", "unix": NOW}},
    }
    built = candidate("durov", 1000, post)
    assert repr(built) == 'Candidate(channel="durov", post_id=42)'
    items, _ = native.rank([built], options())
    assert len(items) == 1


def test_candidate_tolerates_missing_fields() -> None:
    items, _ = native.rank([candidate("a", 1, {"id": 1})], options())
    assert items[0].index == 0


# --- ranking properties ------------------------------------------------------


def test_every_post_appears_once_with_bounded_score() -> None:
    candidates = [make(f"c{i % 4}", i, hours_ago=i % 30, views=100 * (i + 1)) for i in range(80)]
    items, _ = native.rank(candidates, options())
    assert sorted(item.index for item in items) == list(range(80))
    assert all(0.0 <= item.score <= 10.0 for item in items)


def test_empty_feed() -> None:
    assert native.rank([], options()) == ([], [])


def test_newer_post_wins_when_equal() -> None:
    items, _ = native.rank(
        [make("a", 1, hours_ago=48), make("b", 2, hours_ago=1)], options(diversity=0)
    )
    assert items[0].index == 1


def test_half_life_controls_freshness_weight() -> None:
    candidates = [make("a", 1, hours_ago=1, views=100), make("b", 2, hours_ago=20, views=5000)]
    fast, _ = native.rank(candidates, options(diversity=0, half_life_hours=1))
    slow, _ = native.rank(candidates, options(diversity=0, half_life_hours=1000))
    assert fast[0].index == 0
    assert slow[0].index == 1


def test_engagement_is_relative_to_channel() -> None:
    candidates = []
    for i in range(4):
        candidates.append(make("small", i, views=100, subscribers=1_000))
        candidates.append(make("big", 10 + i, views=200_000, subscribers=5_000_000))
    candidates.append(make("small", 99, views=2_000, subscribers=1_000))  # 20x its usual
    candidates.append(make("big", 98, views=200_000, subscribers=5_000_000))  # ordinary
    items, _ = native.rank(candidates, options(diversity=0))
    assert candidates_index(items[0].index, candidates) == ("small", 99)


def candidates_index(index: int, candidates: list[native.Candidate]) -> tuple[str, int]:
    text = repr(candidates[index])
    channel = text.split('channel="')[1].split('"')[0]
    post_id = int(text.split("post_id=")[1].rstrip(")"))
    return channel, post_id


def test_negative_reactions_hurt() -> None:
    good = make("a", 1, positive_reactions=100)
    bad = make("b", 2, negative_reactions=100)
    items, _ = native.rank([bad, good], options(diversity=0))
    assert items[0].index == 1
    assert items[0].score > items[1].score


def test_richer_content_helps() -> None:
    plain = make("a", 1)
    rich = make("b", 2, photos=3, videos=1, text=STORY)
    items, _ = native.rank([plain, rich], options(diversity=0))
    assert items[0].index == 1


def test_duplicates_collapse_into_best_post() -> None:
    candidates = [
        make("a", 1, views=300, text=STORY),
        make("b", 2, views=9000, text=f"⚡️ Срочно! {STORY}"),
        make(
            "c",
            3,
            views=800,
            text="Погода на выходные: солнечно, без осадков, до двадцати градусов тепла днём",
        ),
    ]
    items, _ = native.rank(candidates, options())
    assert len(items) == 2
    merged = next(item for item in items if item.duplicates)
    assert len(merged.duplicates) == 1
    assert {merged.index, merged.duplicates[0]} == {0, 1}


def test_duplicate_collapsing_can_be_disabled() -> None:
    candidates = [make("a", 1, text=STORY), make("b", 2, text=STORY)]
    items, _ = native.rank(candidates, options(collapse_duplicates=False))
    assert len(items) == 2


def test_short_texts_are_never_merged() -> None:
    candidates = [make("a", 1, text="Доброе утро!"), make("b", 2, text="Доброе утро!")]
    items, _ = native.rank(candidates, options())
    assert len(items) == 2


def test_diversity_interleaves_channels() -> None:
    candidates = [make("loud", i, views=50_000) for i in range(8)]
    candidates += [make("quiet", 100 + i, views=40_000) for i in range(4)]

    def longest_run(diversity: float) -> int:
        items, _ = native.rank(candidates, options(diversity=diversity))
        channels = [candidates_index(item.index, candidates)[0] for item in items]
        best = run = 1
        for previous, current in itertools.pairwise(channels):
            run = run + 1 if current == previous else 1
            best = max(best, run)
        return best

    assert longest_run(0.9) < longest_run(0.0)


def test_topics_group_related_posts() -> None:
    candidates = [
        make(
            "a", 1, text="Запуск ракеты с космодрома перенесли, старт ракеты состоится завтра утром"
        ),
        make(
            "b", 2, text="Футбольный матч сборной завершился победой, гол забит на последней минуте"
        ),
        make("c", 3, text="Старт ракеты с космодрома: запуск ракеты назначен на утро"),
    ]
    items, topics = native.rank(candidates, options(collapse_duplicates=False))
    topic = {item.index: item.topic for item in items}
    assert topic[0] == topic[2] != topic[1]
    assert topic[0] is not None
    assert any(word.startswith("ракет") for word in topics[topic[0]].keywords)
    assert sorted(topics[topic[0]].members) == [0, 2]


def test_ranking_is_deterministic() -> None:
    candidates = [make(f"c{i % 3}", i, text="одинаковый текст новости дня") for i in range(30)]
    first, _ = native.rank(candidates, options())
    second, _ = native.rank(candidates, options())
    assert [(i.index, i.score) for i in first] == [(i.index, i.score) for i in second]


def test_large_feed_is_fast() -> None:
    candidates = [
        make(f"c{i % 100}", i, hours_ago=i % 48, views=1000 + i, text=f"{STORY} номер {i % 37}")
        for i in range(2000)
    ]
    started = time.perf_counter()
    items, _ = native.rank(candidates, options())
    elapsed = time.perf_counter() - started
    assert len(items) <= 2000
    assert elapsed < 2.0, f"ranking 2000 posts took {elapsed:.2f}s"
