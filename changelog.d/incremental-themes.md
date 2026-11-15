- **Themes keep their shape and cost less:** after a glossary rebuild, only the terms in no theme yet are sent to the
  model and filed into the category's existing themes, instead of regrouping the whole category for every new term.
  A category with no themes yet, or more than half new, is still grouped from scratch, and
  `interlatch glossary --themes --force` regroups them all ([Themes](docs/dashboard.md#themes)).
