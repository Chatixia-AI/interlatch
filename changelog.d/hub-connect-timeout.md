- **A hub you can't reach no longer slows every sync:** when the hub didn't answer at all (a company VPN you aren't
  on, a hub that is switched off), each background sync waited about two and a half minutes on it before moving on,
  so new sessions showed up late. Interlatch now gives up connecting after 10 seconds and tries again at the next
  sync.
