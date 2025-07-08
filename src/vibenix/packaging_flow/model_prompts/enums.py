"""Enums used in model prompts."""

from enum import Enum


class RefinementExit(Enum):
    """Enum to represent exit conditions for refinement/evaluation."""
    ERROR = "error"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"


class PackagingFailure(Enum):
    """Represents a packaging failure with specific details."""
    BUILD_TOOL_NOT_IN_NIXPKGS = "BUILD_TOOL_NOT_IN_NIXPKGS"
    BUILD_TOOL_VERSION_NOT_IN_NIXPKGS = "BUILD_TOOL_VERSION_NOT_IN_NIXPKGS"
    DEPENDENCY_NOT_IN_NIXPKGS = "DEPENDENCY_NOT_IN_NIXPKGS"
    PACKAGING_REQUIRES_PATCHING_OF_SOURCE = "PACKAGING_REQUIRES_PATCHING_OF_SOURCE"
    BUILD_DOWNLOADS_FROM_NETWORK = "BUILD_DOWNLOADS_FROM_NETWORK"
    REQUIRES_SPECIAL_HARDWARE = "REQUIRES_SPECIAL_HARDWARE"
    REQUIRES_PORT_OR_DOES_NOT_TARGET_LINUX = "REQUIRES_PORT_OR_DOES_NOT_TARGET_LINUX"
    OTHER = "OTHER"