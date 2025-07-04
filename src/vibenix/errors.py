"""Error types for the vibenix build system."""

from enum import Enum
from pydantic import BaseModel
from typing import Optional

class NixBuildErrorDiff(Enum):
    REGRESS = "REGRESS"
    PROGRESS = "PROGRESS"

class NixErrorKind(Enum):
    EVAL_ERROR = "EVAL_ERROR"
    BUILD_ERROR = "BUILD_ERROR"
    HASH_MISMATCH = "HASH_MISMATCH"

class NixError(BaseModel):
    type: NixErrorKind
    error_message: str


class NixBuildResult(BaseModel):
    """Result of a Nix build operation."""
    success: bool
    is_src_attr_only: bool
    error: Optional[NixError] = None

class Solution(BaseModel):
    """Represents a solution candidate with its code and build result."""
    code: str
    result: NixBuildResult
