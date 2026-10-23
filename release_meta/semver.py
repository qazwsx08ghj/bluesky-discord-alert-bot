"""SemVer 2.0 versions (https://semver.org/spec/v2.0.0.html), as used by this project's tags.

    1.0.0 < 1.0.1-rc.1 < 1.0.1-rc.2 < 1.0.1-rc.10 < 1.0.1 < 1.1.0

Strict: three numbers without leading zeros, an optional pre-release part (dot-separated
identifiers, numeric ones without leading zeros). Build metadata ("+...") is refused: two
versions that differ only in build metadata have the same precedence, so they couldn't be told
apart when choosing what to install.
"""
import re
from dataclasses import dataclass
from functools import total_ordering

from . import MetadataError

_NUM = r"(0|[1-9]\d*)"
_IDENT = r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
_PATTERN = re.compile(rf"^{_NUM}\.{_NUM}\.{_NUM}(?:-({_IDENT}(?:\.{_IDENT})*))?$")


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple = ()

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def __str__(self):
        core = f"{self.major}.{self.minor}.{self.patch}"
        return core + ("-" + ".".join(str(p) for p in self.prerelease) if self.prerelease else "")

    def _key(self):
        # A version without a pre-release part ranks above any pre-release of it. Numeric
        # identifiers rank below alphanumeric ones and compare as numbers.
        pre = tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in self.prerelease)
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, pre)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()


def parse(text) -> Version:
    """A Version, or MetadataError if text isn't a strict SemVer 2.0 version without build metadata."""
    if not isinstance(text, str):
        raise MetadataError(f"a version must be text, not {type(text).__name__}")
    match = _PATTERN.fullmatch(text)  # fullmatch: "$" alone would accept a trailing newline
    if not match:
        raise MetadataError(f"{text!r} isn't a version such as 1.2.0 or 1.2.0-rc.1")
    major, minor, patch, pre = match.groups()
    prerelease = tuple(int(p) if p.isdigit() else p for p in pre.split(".")) if pre else ()
    return Version(int(major), int(minor), int(patch), prerelease)


def is_valid(text) -> bool:
    try:
        parse(text)
    except MetadataError:
        return False
    return True
