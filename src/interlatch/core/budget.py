"""Text for an agent's context that fits a character limit by leaving out whole items, least useful first, and says
what it left out, so the agent knows there is more to search for. A cut never lands mid-line unless even the headings
and that last line don't fit, and the same input always gives the same text."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Block:
    """A heading and its items. Each item is (value, group, text): the lowest value goes first when the text is over
    its limit, and `group` names what it is in the "not shown" line. `inline` puts the items on the heading's own
    line, joined by "; "."""

    heading: str | None
    items: list[tuple[int, str, str]] = field(default_factory=list)
    inline: bool = False


def fit(header: list[str], blocks: list[Block], footer: Callable[[dict[str, int]], str], limit: int,
        left_out: dict[str, int] | None = None) -> str:
    """`header` lines, then each block with the items still in, then `footer(left_out)`, at most `limit` characters.
    `left_out` starts with what the caller already left out (counted per group) and grows with each item dropped."""
    kept = {(b, i) for b, block in enumerate(blocks) for i in range(len(block.items))}
    left = dict(left_out or {})

    def render() -> str:
        lines = list(header)
        for b, block in enumerate(blocks):
            shown = [text for i, (_, _, text) in enumerate(block.items) if (b, i) in kept]
            if not shown:
                continue
            if block.inline:
                lines.append((block.heading or "") + "; ".join(shown))
            else:
                lines += ([block.heading] if block.heading else []) + shown
        lines.append(footer(left))
        return "\n".join(line for line in lines if line)

    def weakest():  # the lowest value; on a tie, the later block, then the later item
        return min(((value, -b, -i, group) for b, block in enumerate(blocks)
                    for i, (value, group, _) in enumerate(block.items) if (b, i) in kept), default=None)

    text = render()
    while len(text) > limit and (drop := weakest()):
        kept.discard((-drop[1], -drop[2]))
        left[drop[3]] = left.get(drop[3], 0) + 1
        text = render()
    return text[:limit]


def counted(left_out: dict[str, int], order: tuple[str, ...]) -> str:
    """'3 notes, 1 session': what was left out, in `order`, with plurals."""
    return ", ".join(f"{n} {what}{'s' if n > 1 else ''}" for what in order if (n := left_out.get(what)))
