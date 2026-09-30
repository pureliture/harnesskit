# Third-party notice sanitation policy

Public sanitation reports raw email addresses and plaintext `http://` URLs as
findings. Third-party license texts legitimately contain both: author contact
addresses and license URLs that the upstream text spells with `http://`.
License texts must be reproduced verbatim, so they cannot be rewritten.

This policy approves only these exact bytes, pinned by Git blob OID and content
SHA-256 in `publish/public-sanitation-allowlist.yml`:

- upstream license texts under `assets/legal/license-texts/`
- generated `THIRD_PARTY_NOTICES.md` and the two bundled HTML notices, produced
  by `scripts/package/generate_third_party_notices.py` from the same inventory
- `src-tauri/tauri.conf.json`, whose CSP contains Tauri's loopback IPC origin
  (`ipc.localhost` over plain HTTP)

It approves no other file. Regenerating a notice or changing a license text
changes its hash, so the new bytes must be reviewed again before publication.
The notices contain no private paths, private repository URLs, credentials, or
HarnessKit contributor personal data.
