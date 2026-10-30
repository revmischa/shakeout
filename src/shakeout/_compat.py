"""Small shims for older supported Python versions (no extra runtime dependencies)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if sys.version_info >= (3, 12):
    from typing import override
elif TYPE_CHECKING:  # type checkers understand typing_extensions; nothing is imported at runtime
    from typing_extensions import override
else:  # pragma: no cover - exercised on 3.10/3.11 only

    def override(func, /):  # noqa: ANN001, ANN202 - typed via typing_extensions above
        """Mark a method as overriding its base (typing.override before 3.12)."""
        return func


__all__ = ["override"]
