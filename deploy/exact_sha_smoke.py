#!/usr/bin/env python3
"""Headless normal-route Claude + Codex execution proof for ADR-0013."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Any

from omnigent_client import OmnigentClient, SessionsChat


PROMPT = """CUTOVER EXECUTION PROOF. Use the normal Program dispatch path.
Dispatch `architect` and `staff` exactly once each with title
`bundle-echo-proof` and args exactly `{input: "For the public bundle echo
protocol, use your configured response. If no different response is configured,
reply \u0060NIGHTSHIFT_BUNDLE_ECHO\u0060. Do not use tools or add other text.",
purpose: explore, cost_budget: {max_cost_usd: 2}}`. The other fields are dispatch
metadata. Do not include any response-marker value. Do not set a timer. Collect
both replies and return them with the child names. Do not inspect Git, files,
process environment, caches, or the candidate checkout. This turn is proof only;
do no project work."""

CONTINUE_PROMPT = """Continue the same cutover execution proof. Do not dispatch
either child again. Wait for the existing `architect` and `staff` children if
needed, read their persisted replies from your inbox, and return both replies
verbatim with child names. No echo-token value is included in this request. Do not
inspect Git, files, process environment, caches, or the candidate checkout."""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


async def _persisted_children_ready(client: OmnigentClient,
                                    server: str, session_id: str) -> bool:
    response = await client._http.get(
        f"{server.rstrip('/')}/v1/sessions/{session_id}/child_sessions"
    )
    response.raise_for_status()
    payload = response.json()
    children = payload.get("data", payload) if isinstance(payload, dict) else payload
    by_name = {
        str(row.get("tool") or row.get("agent_name") or ""): row
        for row in children if isinstance(row, dict)
    }
    for name, marker in (
        ("architect", "NIGHTSHIFT_BUNDLE_ECHO"),
        ("staff", "NS-EXEC-ADR0013-CLAUDE-4C2D8F70"),
    ):
        child_id = (by_name.get(name) or {}).get("id")
        if not child_id:
            return False
        response = await client._http.get(
            f"{server.rstrip('/')}/v1/sessions/{child_id}/items?limit=1000"
        )
        response.raise_for_status()
        items_payload = response.json()
        items = items_payload.get("data", items_payload)
        assistant = "\n".join(
            text
            for item in items if isinstance(item, dict)
            and item.get("type") == "message" and item.get("role") == "assistant"
            for text in _strings(item.get("content"))
        )
        if marker not in assistant:
            return False
    return True


async def run(args: argparse.Namespace) -> dict[str, Any]:
    async with OmnigentClient(base_url=args.server) as client:
        # create_from_agent_id deliberately leaves host selection to interactive
        # clients. A headless proof must bind the already-proven production host
        # explicitly or the first message fails with "No runner bound".
        response = await client._http.post(  # pinned Omnigent 0.10.0 seam
            f"{args.server.rstrip('/')}/v1/sessions",
            json={
                "agent_id": args.agent_id,
                "title": "Nightshift exact-SHA Claude + Codex execution proof",
                "workspace": args.workspace,
                "host_id": args.host_id,
                "labels": {"nightshift.proof": "exact-sha-executed"},
            },
        )
        response.raise_for_status()
        created = response.json()
        session = await client.sessions.get(created["id"])
        chat = SessionsChat(
            namespace=client.sessions,
            files_uploader=None,
            files_getter=None,
            session=session,
        )
        deadline = asyncio.get_running_loop().time() + args.timeout
        responses: list[str] = []
        result = await asyncio.wait_for(chat.query(PROMPT), timeout=args.timeout)
        responses.append(result.text)
        for _round in range(4):
            if await _persisted_children_ready(client, args.server, session.id):
                break
            while await chat.tree_busy():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError("child subtree did not quiesce before smoke timeout")
                await asyncio.sleep(min(5.0, remaining))
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("execution markers not collected before smoke timeout")
            result = await asyncio.wait_for(chat.query(CONTINUE_PROMPT), timeout=remaining)
            responses.append(result.text)
        if not await _persisted_children_ready(client, args.server, session.id):
            raise TimeoutError("persisted child records never returned both bundle markers")
        response_text = "\n".join(responses).encode()
        return {
            "session_id": session.id,
            "root_response_sha256": hashlib.sha256(response_text).hexdigest(),
            "root_response_bytes": len(response_text),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--timeout", type=float, default=780.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
