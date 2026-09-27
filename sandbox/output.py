"""Bounded capture of command output: keep the head and the tail, count the rest."""

from dataclasses import dataclass, field

TRUNCATION_MARKER = "\n[... {skipped} bytes of output omitted ...]\n"


@dataclass
class BoundedBuffer:
    """Keeps at most ``limit`` bytes: the first half and the most recent half.

    Test runners print their summary last, so the tail matters as much as the head.
    """

    limit: int
    total_bytes: int = 0
    _head: bytearray = field(default_factory=bytearray)
    _tail: bytearray = field(default_factory=bytearray)

    def write(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        head_room = self.limit // 2 - len(self._head)
        if head_room > 0:
            self._head += chunk[:head_room]
            chunk = chunk[head_room:]
        if chunk:
            self._tail += chunk
            tail_limit = self.limit - self.limit // 2
            if len(self._tail) > tail_limit:
                del self._tail[: len(self._tail) - tail_limit]

    @property
    def truncated(self) -> bool:
        return self.total_bytes > len(self._head) + len(self._tail)

    def text(self) -> str:
        head = self._head.decode("utf-8", "replace")
        tail = self._tail.decode("utf-8", "replace")
        if not self.truncated:
            return head + tail
        skipped = self.total_bytes - len(self._head) - len(self._tail)
        return head + TRUNCATION_MARKER.format(skipped=skipped) + tail
