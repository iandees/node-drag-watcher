"""Checker plugin framework for detecting OSM data issues."""

import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Action:
    """A single element action from an augmented diff."""
    action_type: str           # "create", "modify", "delete"
    element_type: str          # "node", "way", "relation"
    element_id: str
    version: str
    changeset: str
    user: str
    tags_old: dict[str, str] = field(default_factory=dict)
    tags_new: dict[str, str] = field(default_factory=dict)
    # Node geometry
    coords_old: tuple[float, float] | None = None
    coords_new: tuple[float, float] | None = None
    # Way geometry
    nd_refs_old: list[str] | None = None
    nd_refs_new: list[str] | None = None
    node_coords_old: dict[str, tuple[float, float]] | None = None
    node_coords_new: dict[str, tuple[float, float]] | None = None


@dataclass
class Issue:
    """A detected issue with an OSM element."""
    element_type: str
    element_id: str
    element_version: str
    changeset: str
    user: str
    check_name: str
    summary: str
    tags_before: dict[str, str] = field(default_factory=dict)
    tags_after: dict[str, str] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


class BaseChecker(ABC):
    """Base class for all checkers."""

    @abstractmethod
    def check(self, action: Action) -> list[Issue]:
        """Check a single action for issues. Returns list of Issues found."""
        ...
