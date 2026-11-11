"""Registered producer credentials for Publication Gateway tests (audit C8).

`stage()` authenticates `producer_identity` against the tenant's identity
registry, so a test that stages as someone needs that someone's token. This
registers an identity on first use under a token derived from its name, and
refuses loudly when the identity was already registered with another token:
such a test (a reviewer staging its own work) must pass that token itself.
"""


def producer_token(pub, cx, tenant_id, identity, *, t=0.0):
    canonical = pub.canonicalize_identity(identity)
    token = b"producer-token:" + canonical.encode("utf-8")
    live = cx.execute(
        "SELECT token_hash FROM publication_reviewer WHERE tenant_id=? "
        "AND reviewer_identity=? AND revoked_at IS NULL",
        (tenant_id, canonical)).fetchone()
    if live is None:
        pub.register_reviewer(cx, tenant_id=tenant_id, reviewer_identity=identity,
                              token_bytes=token, t=t)
    elif live[0] != pub.sha256_hex(token):
        raise AssertionError(
            "%r is registered with another token; pass that token to stage()" % identity)
    return token
