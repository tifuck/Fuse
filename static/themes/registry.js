import { createConsoleTheme } from './console/index.js';
import { createCombinedTheme } from './combined/index.js';

// One presentation per page. The server picks it from the fuse_chat_view
// cookie and stamps html[data-chat-view]; each theme owns its folder, its
// rendering and its styles, and returns the same public surface.
export const VIEW = document.documentElement.dataset.chatView === 'combined' ? 'combined' : 'console';
export const createTheme = VIEW === 'combined' ? createCombinedTheme : createConsoleTheme;
