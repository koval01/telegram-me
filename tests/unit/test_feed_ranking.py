"""The native (Rust) feed: cached pages in, ranked `/v1/feed` body out.

Rust has its own unit tests (`cargo test`), including the ranking signals
(`1.2K` counters, reactions, media); these tests pin the behaviour as seen
from Python, through the real extension module and the same page JSON the
page cache holds.
"""

import itertools
import time
from typing import Any

import telegram_me_native as native
from pydantic_core import from_json, to_json

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

type Post = tuple[str, int, dict[str, Any]]


def make(
    channel: str,
    post_id: int,
    hours_ago: float = 1,
    views: int | str = 1000,
    *,
    subscribers: int = 10_000,
    text: str = "",
    reacts: list[dict[str, Any]] | None = None,
    media: tuple[str, ...] = (),
) -> Post:
    """A post as the parser caches it, with its channel's subscriber count."""
    content: dict[str, Any] = {}
    if text:
        content["text"] = {"string": text, "html": text}
    if media:
        content["media"] = [{"type": kind} for kind in media]
    if reacts:
        content["reacts"] = reacts
    post = {
        "id": post_id,
        "content": content,
        "footer": {
            "views": str(views),
            "date": {"string": "", "unix": int(NOW - hours_ago * HOUR)},
        },
        "view": "",
    }
    return channel, subscribers, post


def pages(posts: list[Post]) -> list[tuple[str, bytes]]:
    """Group posts into one cached page per channel, in order of appearance."""
    grouped: dict[str, dict[str, Any]] = {}
    for channel, subscribers, post in posts:
        page = grouped.setdefault(
            channel,
            {
                "channel": {"username": channel, "counters": {"subscribers": str(subscribers)}},
                "content": {"posts": []},
            },
        )
        page["content"]["posts"].append(post)
    return [(channel, to_json(page)) for channel, page in grouped.items()]


def options(**overrides: Any) -> native.RankOptions:
    return native.RankOptions(now=NOW, **overrides)


def rank(posts: list[Post], **overrides: Any) -> list[dict[str, Any]]:
    feed, unreadable = native.build_feed(pages(posts), options(**overrides))
    assert unreadable == []
    result: list[dict[str, Any]] = from_json(feed)["result"]
    return result


def ids(entries: list[dict[str, Any]]) -> list[tuple[str, int]]:
    return [(entry["channel"]["username"], entry["id"]) for entry in entries]


def test_every_post_appears_once_with_bounded_score() -> None:
    posts = [make(f"c{i % 4}", i, hours_ago=i % 30, views=100 * (i + 1)) for i in range(80)]
    entries = rank(posts)
    assert sorted(entry["id"] for entry in entries) == list(range(80))
    assert all(0.0 <= entry["_score"] <= 10.0 for entry in entries)


def test_empty_feed() -> None:
    assert native.build_feed([], options()) == (b'{"result":[]}', [])


def test_entries_are_posts_with_channel_and_score() -> None:
    (entry,) = rank([make("durov", 1, text="hello")])
    assert list(entry) == ["id", "content", "footer", "view", "channel", "_score"]
    assert entry["channel"] == {"username": "durov", "counters": {"subscribers": "10000"}}


def test_unreadable_pages_are_reported() -> None:
    good = pages([make("good", 1)])
    feed, unreadable = native.build_feed([("bad", b"{not json"), *good, ("list", b"[]")], options())
    assert unreadable == [0, 2]
    assert ids(from_json(feed)["result"]) == [("good", 1)]


def test_compact_counters_reach_the_ranker() -> None:
    posts = [make("a", i, views="1.2K") for i in range(3)] + [make("a", 3, views="15 400")]
    assert ids(rank(posts, diversity=0))[0] == ("a", 3)


def test_newer_post_wins_when_equal() -> None:
    entries = rank([make("a", 1, hours_ago=48), make("b", 2, hours_ago=1)], diversity=0)
    assert ids(entries)[0] == ("b", 2)


def test_half_life_controls_freshness_weight() -> None:
    posts = [make("a", 1, hours_ago=1, views=100), make("b", 2, hours_ago=20, views=5000)]
    assert ids(rank(posts, diversity=0, half_life_hours=1))[0] == ("a", 1)
    assert ids(rank(posts, diversity=0, half_life_hours=1000))[0] == ("b", 2)


def test_engagement_is_relative_to_channel() -> None:
    posts = []
    for i in range(4):
        posts.append(make("small", i, views=100, subscribers=1_000))
        posts.append(make("big", 10 + i, views=200_000, subscribers=5_000_000))
    posts.append(make("small", 99, views=2_000, subscribers=1_000))  # 20x its usual
    posts.append(make("big", 98, views=200_000, subscribers=5_000_000))  # ordinary
    assert ids(rank(posts, diversity=0))[0] == ("small", 99)


def test_negative_reactions_hurt() -> None:
    good = make("a", 1, reacts=[{"type": "emoji", "emoji": "👍", "count": "100"}])
    bad = make("b", 2, reacts=[{"type": "emoji", "emoji": "👎", "count": "100"}])
    entries = rank([bad, good], diversity=0)
    assert ids(entries)[0] == ("a", 1)
    assert entries[0]["_score"] > entries[1]["_score"]


def test_richer_content_helps() -> None:
    plain = make("a", 1)
    rich = make("b", 2, media=("image", "image", "image", "video"), text=STORY)
    assert ids(rank([plain, rich], diversity=0))[0] == ("b", 2)


def test_duplicates_collapse_into_best_post() -> None:
    posts = [
        make("a", 1, views=300, text=STORY),
        make("b", 2, views=9000, text=f"⚡️ Срочно! {STORY}"),
        make("c", 3, views=800, text=WEATHER),
    ]
    entries = rank(posts)
    assert len(entries) == 2
    merged = next(entry for entry in entries if "_duplicates" in entry)
    (duplicate,) = merged["_duplicates"]
    assert {("a", 1), ("b", 2)} == {
        (merged["channel"]["username"], merged["id"]),
        (duplicate["channel"], duplicate["id"]),
    }


def test_duplicate_collapsing_can_be_disabled() -> None:
    posts = [make("a", 1, text=STORY), make("b", 2, text=STORY)]
    assert len(rank(posts, collapse_duplicates=False)) == 2


def test_short_texts_are_never_merged() -> None:
    posts = [make("a", 1, text="Доброе утро!"), make("b", 2, text="Доброе утро!")]
    assert len(rank(posts)) == 2


def test_same_post_of_one_channel_is_listed_once() -> None:
    feed, _ = native.build_feed(pages([make("a", 1)]) * 2, options())
    assert len(from_json(feed)["result"]) == 1


def test_diversity_interleaves_channels() -> None:
    posts = [make("loud", i, views=50_000) for i in range(8)]
    posts += [make("quiet", 100 + i, views=40_000) for i in range(4)]

    def longest_run(diversity: float) -> int:
        channels = [channel for channel, _ in ids(rank(posts, diversity=diversity))]
        best = run = 1
        for previous, current in itertools.pairwise(channels):
            run = run + 1 if current == previous else 1
            best = max(best, run)
        return best

    assert longest_run(0.9) < longest_run(0.0)


def test_topics_group_related_posts() -> None:
    posts = [make("a", 1, text=ROCKET_1), make("b", 2, text=FOOTBALL), make("c", 3, text=ROCKET_2)]
    entries = {entry["id"]: entry for entry in rank(posts, collapse_duplicates=False)}
    assert "_topic" not in entries[2]
    assert entries[1]["_topic"] == entries[3]["_topic"]
    assert any(word.startswith("ракет") for word in entries[1]["_topic"]["keywords"])


def test_ranking_is_deterministic() -> None:
    posts = [make(f"c{i % 3}", i, text="одинаковый текст новости дня") for i in range(30)]
    first = native.build_feed(pages(posts), options())
    assert native.build_feed(pages(posts), options()) == first


def test_large_feed_is_fast() -> None:
    posts = [
        make(f"c{i % 100}", i, hours_ago=i % 48, views=1000 + i, text=f"{STORY} номер {i % 37}")
        for i in range(2000)
    ]
    encoded = pages(posts)
    started = time.perf_counter()
    feed, _ = native.build_feed(encoded, options())
    elapsed = time.perf_counter() - started
    assert len(from_json(feed)["result"]) <= 2000
    assert elapsed < 2.0, f"building a feed of 2000 posts took {elapsed:.2f}s"
