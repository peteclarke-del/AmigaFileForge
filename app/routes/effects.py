from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, TypeVar


MutationTarget = Literal["path", "targetImage"]
View = TypeVar("View", bound=Callable)


@dataclass(frozen=True, slots=True)
class ImageMutation:
    """Checkpoint metadata owned by the route that changes an image."""

    reason: str
    target: MutationTarget = "path"


ATTRIBUTE = "__amiga_image_mutation__"
EFFECT_ATTRIBUTE = "__amiga_request_effect__"


@dataclass(frozen=True, slots=True)
class RequestEffect:
    kind: Literal["image-mutation", "lifecycle", "read-only", "external"]
    reason: str


def image_mutation(reason: str, *, target: MutationTarget = "path"):
    """Declare that a route mutates an image and needs an undo checkpoint."""

    def decorate(view: View) -> View:
        setattr(view, ATTRIBUTE, ImageMutation(reason=reason, target=target))
        setattr(view, EFFECT_ATTRIBUTE, RequestEffect("image-mutation", reason))
        return view

    return decorate


def mutation_for(view: Callable | None) -> ImageMutation | None:
    return getattr(view, ATTRIBUTE, None) if view is not None else None


def request_effect(
    kind: Literal["lifecycle", "read-only", "external"], reason: str
):
    """Declare why an unsafe HTTP method intentionally needs no image checkpoint."""

    def decorate(view: View) -> View:
        setattr(view, EFFECT_ATTRIBUTE, RequestEffect(kind, reason))
        return view

    return decorate


def effect_for(view: Callable | None) -> RequestEffect | None:
    return getattr(view, EFFECT_ATTRIBUTE, None) if view is not None else None
