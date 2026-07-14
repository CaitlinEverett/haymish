"""People-tag hygiene report: unnamed faces, faces without persons.

Report-only — surfaces where Photos' face tagging needs human attention.
"""

from __future__ import annotations

from ..types import Candidate, DetectorResult

UNKNOWN = "_UNKNOWN_"


def detect(photos: list) -> DetectorResult:
    named_persons: set[str] = set()
    with_unnamed_faces = 0
    faces_no_person = 0
    for p in photos:
        persons = getattr(p, "persons", None) or []
        faces = getattr(p, "face_info", None) or []
        named = [x for x in persons if x and x != UNKNOWN]
        named_persons.update(named)
        if UNKNOWN in persons:
            with_unnamed_faces += 1
        if faces and not named:
            faces_no_person += 1
    return DetectorResult(
        name="people",
        title="People-tag hygiene",
        candidates=[],  # report-only; counts live in stats
        stats={
            "named_people": len(named_persons),
            "photos_with_unnamed_faces": with_unnamed_faces,
            "photos_with_faces_but_nobody_named": faces_no_person,
        },
    )
