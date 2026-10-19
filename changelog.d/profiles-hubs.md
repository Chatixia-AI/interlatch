- **Profiles: one computer, several hubs.** A named profile joins a hub of its own
  (`interlatch --profile Acme hub join …`) and sends it only the sessions it owns, read from your one archive, with its
  own token and computer ID. Teammates' lessons from each hub reach only that profile's folders, and `interlatch push`
  and the background job send for every hub ([Connect a profile to its hub](docs/profiles.md#connect-a-profile-to-its-hub)).
