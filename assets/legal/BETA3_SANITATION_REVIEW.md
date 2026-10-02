# Beta 3 exact sanitation review

Owner approval: pureliture explicitly approved the 21 remaining sanitation
findings reviewed for the beta publication on 2026-10-02.

## Scope and reasons

This review authorizes exactly 21 additional tuples in
`publish/public-sanitation-allowlist.yml`. Each tuple binds a rule, a surface,
a locator SHA-256 and a complete value SHA-256. The appended entries bind their
justification SHA-256 to the bytes of this document and name pureliture as the
approver. Prior entries and the existing notice sanitation policy are unchanged.

- Baseline Git: six findings covering four previously reviewed public identity
  values, one historical public coauthor message and one existing icon path.
- Candidate Git delta: ten findings covering four previously reviewed historical
  public identity values, the same historical coauthor message, the same icon
  path, and four findings for the newly generated Markdown and HTML notices.
- GitHub: five findings covering the public pull-request and beta 2 release
  author, two findings for the existing beta 2 notice asset, and one binary
  review finding for the existing beta 2 disk image.

The identity and path allowances apply only to their exact reviewed locations
and values; they do not authorize future identities, messages or paths. The
historical coauthor message is already public and preserves attribution.

The updated notices retain upstream license text verbatim. Their full-body
hashes changed with the inventory and generated layout; their contact-address
and plaintext-link sets are unchanged from the reviewed notices. The beta 2
notice asset has the same complete bytes as the previously reviewed notice.
No license text is rewritten by this review.

The beta 2 disk-image allowance covers only the existing asset whose SHA-256 is
`1c2409b43777fb36b23972c6e4f383ea10669c95c82950c19a0bbf2c5233f991`.
It is a sanitation approval of that exact existing binary, not a claim that its
internal runtime content has been examined or that beta 3 packaging is verified.

## Boundaries

No wildcard or value-only allowance is granted. Personal filesystem paths,
credentials, private repository references, private branches and internal
service addresses remain prohibited. Scanner rules and publication guards are
unchanged. Any additional finding requires a separate explicit owner decision.
This review does not authorize a push, release, deployment or runtime-setting
change.
