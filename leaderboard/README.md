# The leaderboard's backend

The usage leaderboard (`src/chronicle/leaderboard.py`, the dashboard's **Leaderboard** page) keeps one row per person in
Cloud Firestore in the Firebase project `interlatch` (database `(default)`, `asia-northeast1`). Interlatch talks to it
over REST.

- `firestore.rules`: anyone may read the board (100 rows per query at most). A signed-in Google (or GitHub) user may
  write only their own row, `players/{uid}`, with a fixed set of bounded fields and `at` set to the server time. Their
  provider id must be the one in the sign-in token.
- `public/signin.html`, served at `https://interlatch.firebaseapp.com/signin.html`: the dashboard opens it with its own
  loopback address and a one-time state. It signs in with Google through Firebase Auth (in memory only), then goes
  back to that dashboard with the refresh token in the URL fragment. The dashboard keeps the token only if the state
  matches the one it made. The page accepts only `http://127.0.0.1`, `localhost` or `[::1]` as the address to return to.
- Deploy both from this folder: `firebase deploy --only firestore:rules,hosting`.

Setup that lives outside this repository: Google is turned on under Authentication › Sign-in method. Firebase creates
the OAuth client itself, and `interlatch.firebaseapp.com` is an authorized domain by default. The web app
`interlatch-dashboard` holds the project's web config, which Hosting serves at `/__/firebase/init.json`. Interlatch and
the sign-in page read the Firebase web key from there, so it is not in this repository.
