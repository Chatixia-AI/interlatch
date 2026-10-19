- **Each hub has its own sign-in cookie:** two hubs reached under one host name (on different ports, say) no longer
  sign each other's people out: a hub's cookie is named for that hub, with `__Host-` in front over HTTPS. A browser
  still sends one host name's cookies to all its ports, so give every hub a host name of its own, sslip.io names
  included. A browser already signed in stays signed in and moves to the new name on its next visit
  ([One name per hub](docs/devices.md#one-name-per-hub)).
