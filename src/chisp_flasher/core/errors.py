from __future__ import annotations


class ChispError(Exception):
    pass


class ProjectFormatError(ChispError):
    pass


class ResolverError(ChispError):
    pass


class FrameError(ChispError):
    pass


class TransportError(ChispError):
    pass


class BackendError(ChispError):
    pass


class OperationError(ChispError):
    pass
