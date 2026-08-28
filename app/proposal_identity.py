from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
import re
import secrets

from .console_routing import failure_contract


_PROPOSAL_ID = re.compile(
    r"^(?P<timestamp>[0-9]{8}T[0-9]{6})-(?P<random>[0-9a-f]{32})$"
)
PROPOSAL_ID_ATTEMPTS = 4


class ProposalIdentityError(ValueError):
    pass


def generate_proposal_id(created: datetime) -> str:
    return f"{created:%Y%m%dT%H%M%S}-{secrets.token_hex(16)}"


def proposal_id_candidates(created: datetime) -> Iterator[str]:
    for _ in range(PROPOSAL_ID_ATTEMPTS):
        yield generate_proposal_id(created)


@failure_contract(raises=(ProposalIdentityError,))
def require_proposal_id(value: object) -> str:
    if not isinstance(value, str):
        raise ProposalIdentityError("proposal id must be a string")
    match = _PROPOSAL_ID.fullmatch(value)
    if match is None:
        raise ProposalIdentityError("proposal id is non-canonical")
    try:
        datetime.strptime(match.group("timestamp"), "%Y%m%dT%H%M%S")
    except ValueError as exc:
        raise ProposalIdentityError("proposal timestamp is invalid") from exc
    return value


def require_proposal_identity(path: Path, record_id: object) -> str:
    proposal_id = require_proposal_id(record_id)
    if path.name != f"{proposal_id}.yaml" or path.stem != proposal_id:
        raise ProposalIdentityError("proposal id does not match its filename")
    return proposal_id
