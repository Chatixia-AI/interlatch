- **Fixed: the dashboard could hang on a chat with a long tool output:** a ChatGPT chat whose Python run printed a
  long text without spaces (a base64 image, say) took hours to save, and the dashboard answered nothing meanwhile. It
  now takes a fraction of a second. Linking chats to projects is faster too, and the browser extension gives up on a
  dashboard that doesn't answer within a minute and tries again later.
