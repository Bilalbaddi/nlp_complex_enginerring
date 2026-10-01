"""The synthetic data generator: determinism and the planted effects that later phases must be able to find."""

from collections import Counter
from datetime import date
from functools import lru_cache

from app.ingestion import normalize as N
from scripts.datagen import constants as K
from scripts.datagen.generate import Dataset, generate


@lru_cache
def ds(n: int = 6000, seed: int = 42) -> Dataset:
    return generate(n, 3, seed)


def test_deterministic_for_a_seed_and_different_across_seeds() -> None:
    a, b = generate(800, 2, 7), generate(800, 2, 7)
    assert a.complaints == b.complaints and a.feedback == b.feedback and a.performance == b.performance
    c = generate(800, 2, 8)
    assert [r["description"] for r in a.complaints] != [r["description"] for r in c.complaints]


def test_size_and_field_validity() -> None:
    d = ds()
    assert len(d.complaints) == 6000 and len({r["external_id"] for r in d.complaints}) == 6000
    assert {r["category"] for r in d.complaints} <= set(K.CATEGORIES)
    assert {r["language"] for r in d.complaints} == {"en", "hi", "mr", "hinglish"}
    assert {r["status"] for r in d.complaints} <= {"open", "assigned", "in_progress", "resolved", "closed", "rejected"}
    times = [r["submitted_at"] for r in d.complaints]
    assert times == sorted(times) and times[0] >= "2023-09-30" and times[-1] < "2026-09-30"
    assert all(1 <= f["rating"] <= 5 for f in d.feedback)


def _inside(ring: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def test_every_complaint_lies_inside_its_declared_ward() -> None:
    d = ds()
    resolver = N.build_ward_resolver([(i, w["code"], w["name"]) for i, w in enumerate(d.wards, start=1)])
    checked = 0
    for r in d.complaints:
        wid = resolver.resolve(r["ward"])
        if wid is None:
            assert r["ward"].strip() == ""  # only intentionally blank wards are unresolvable
            continue
        assert _inside(d.wards[wid - 1]["ring"], r["longitude"], r["latitude"]), r["external_id"]
        checked += 1
    assert checked > 5000


def test_monsoon_seasonality_is_planted() -> None:
    drainage = Counter(int(r["submitted_at"][5:7]) for r in ds(20000).complaints if r["true_category"] == "drainage")
    monsoon = sum(drainage[m] for m in (6, 7, 8, 9)) / 4
    dry = sum(drainage[m] for m in (1, 2, 3, 4, 11, 12)) / 6
    assert monsoon > 3 * dry
    assert max(drainage, key=drainage.get) in (7, 8)  # type: ignore[arg-type]


def test_bursts_are_localised_and_time_bound() -> None:
    d = ds(20000)
    by_burst = Counter(r["burst"] for r in d.complaints if r["burst"])
    assert set(by_burst) == {b.key for b in K.BURSTS}
    dengue = [r for r in d.complaints if r["burst"] == "dengue"]
    assert {r["true_category"] for r in dengue} == {"public_health"}
    wards = Counter(N.key(r["ward"]) for r in dengue if r["ward"].strip())
    top = {N.key(w["name"]) for w in d.wards if w["code"] in ("W05", "W06")} | {"w05", "w06", "ward 5", "ward 6"}
    assert sum(v for k, v in wards.items() if k in top) / sum(wards.values()) > 0.95
    end = date(2026, 9, 30)
    assert min(r["submitted_at"][:10] for r in dengue) >= str(end.replace(month=8, day=1))  # "emerging": last ~45 days
    truth = {b["key"]: b for b in d.truth["bursts"]}
    assert truth["dengue"]["complaints"] == by_burst["dengue"]


def test_underperforming_wards_and_departments_are_slower() -> None:
    d = ds(20000)
    sla = {dep.category: dep.sla_hours for dep in K.DEPARTMENTS}
    resolver = N.build_ward_resolver([(i, w["code"], w["name"]) for i, w in enumerate(d.wards, start=1)])
    ratio: dict[int, list[float]] = {}
    for r in d.complaints:
        if r["resolution_hours"] != "" and r["status"] != "rejected" and (wid := resolver.resolve(r["ward"])):
            ratio.setdefault(wid, []).append(float(r["resolution_hours"]) / sla[r["true_category"]])
    mean = {wid: sum(v) / len(v) for wid, v in ratio.items()}
    poor = [mean[int(c[1:])] for c in K.WARD_PERFORMANCE if K.WARD_PERFORMANCE[c] > 1.6]
    good = [mean[int(c[1:])] for c in K.WARD_PERFORMANCE if K.WARD_PERFORMANCE[c] < 0.9]
    assert min(poor) > 1.4 * max(good)


def test_near_duplicates_and_label_noise_have_ground_truth() -> None:
    d = ds(10000)
    ids = {r["external_id"]: r for r in d.complaints}
    dups = [r for r in d.complaints if r["true_duplicate_of"]]
    assert 0.02 < len(dups) / len(d.complaints) < 0.04
    for r in dups:
        src = ids[r["true_duplicate_of"]]
        assert src["submitted_at"] < r["submitted_at"] and src["true_category"] == r["true_category"]
    noisy = sum(r["category"] != r["true_category"] for r in d.complaints) / len(d.complaints)
    assert 0.02 < noisy < 0.04


def test_ward_names_are_written_messily_but_resolvably() -> None:
    forms = Counter(
        "blank" if not r["ward"].strip() else "code" if r["ward"].startswith("W") and r["ward"][1:].isdigit()
        else "ward n" if r["ward"].startswith("Ward ") else "name" for r in ds().complaints
    )
    assert forms["name"] > 0.4 * 6000 and forms["code"] > 0 and forms["ward n"] > 0 and 0 < forms["blank"] < 0.06 * 6000


def test_performance_rows_are_consistent() -> None:
    perf = ds().performance
    assert any(not p["ward"] for p in perf) and any(p["ward"] for p in perf)
    for p in perf:
        assert 0 <= p["complaints_resolved"] <= p["complaints_received"] and p["sla_breaches"] <= p["complaints_received"]
    total = sum(p["complaints_received"] for p in perf if p["ward"])
    assert total == 6000  # every complaint is counted exactly once in the per-ward rows
