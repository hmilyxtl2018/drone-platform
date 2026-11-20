"""Credential checks at the persisted Profile boundary."""
from __future__ import annotations

import re


_SECRET_FIELDS = {
    'apikey', 'password', 'passwd', 'authorization', 'bearertoken',
    'accesstoken', 'refreshtoken', 'clientsecret', 'privatekey', 'secret',
}
_REFERENCE_FIELDS = {'credentialref', 'secretref'}


def validate_credential_references(value):
    """Allow environment references and reject raw credential config fields.

    This validates structured configuration, not arbitrary prompt text. Hosts
    must never put credentials into prompts, input records or response text.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
            if normalized in _SECRET_FIELDS:
                raise ValueError('profile configuration contains a credential value field; use a credential reference')
            if normalized in _REFERENCE_FIELDS and child is not None:
                if not isinstance(child, str) or not re.fullmatch(r'(?:env:)?[A-Z][A-Z0-9_]{1,127}', child):
                    raise ValueError('credential reference must name a server environment variable')
            validate_credential_references(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            validate_credential_references(child)
