// One presentation per page. The server picks it from the fuse_chat_view
// cookie and stamps html[data-chat-view]; each theme owns its folder, its
// rendering and its styles, and returns the same public surface. The theme
// list and its order come from static/themes/*/theme.json (#fuse-chat-themes);
// a theme is listed there and has its entry here. Only the page's theme is
// loaded (chat.html preloads its modules); the specifiers stay literal so the
// server can version them.
const THEMES = {
  console: () => import('./console/index.js').then((theme) => theme.createConsoleTheme),
  combined: () => import('./combined/index.js').then((theme) => theme.createCombinedTheme),
  desktop: () => import('./desktop/index.js').then((theme) => theme.createDesktopTheme),
};
let ids = [];
try { ids = JSON.parse(document.getElementById('fuse-chat-themes')?.textContent || '[]').map((meta) => meta.id); } catch (_) {}
ids = ids.filter((id) => THEMES[id]);
const stamped = document.documentElement.dataset.chatView;
export const VIEW = ids.includes(stamped) ? stamped : (ids[0] || 'console');
export const createTheme = await THEMES[VIEW]();
