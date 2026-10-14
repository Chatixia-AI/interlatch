# Interlatch Enterprise

This directory holds the features a company needs to run Interlatch for its engineering teams. It is licensed under
the [Interlatch Enterprise License](LICENSE), not MIT: you can read it, change it and try it out for free, but running
it in production needs a subscription. Everything outside `ee/` stays [MIT](../LICENSE).

## What goes here, and what doesn't

The line follows who asks for the feature.

- **MIT, in `src/interlatch/`:** anything one developer or a small team wants. Recording, analysis through a coding
  agent or any model provider's API (Anthropic, Bedrock, OpenAI, Azure OpenAI, OpenRouter, Ollama), the dashboard,
  the MCP server, and the hub, with its computers, people and roles, invites, shared projects, team store and
  team Home. Everything released before `ee/` existed stays MIT too.
- **Enterprise, in `ee/`:** what the company's IT, security or engineering leadership needs before rolling Interlatch
  out across teams:
  - single sign-on (OIDC, SAML) and user provisioning (SCIM) for the hub
  - policies: retention, redaction rules, folders that are never recorded
  - audit log export to a SIEM
  - a value report for admins: lessons reused, repeat errors caught, adoption
  - several teams under one organization, with org-wide admins

The MIT core never imports from `ee/` and works on its own. When an enterprise feature needs a hook in the core
(for example, a sign-in step for SSO), the hook goes in the core under MIT and the feature goes here.

## How it ships

`ee/` builds its own package, `interlatch-enterprise` (import name `interlatch_ee`), which depends on
`interlatch`. The `interlatch` wheel and source distribution on PyPI don't contain `ee/`, so they remain
MIT only.

```bash
uv pip install -e ee     # into a checkout's environment, for development
```

## Contributing

Pull requests to `ee/` are welcome. By opening one you agree to the contribution terms in section 3 of the
[license](LICENSE).
