// composer feature: composes its submodules and exposes one interface.
import { createInput } from './composer/input.js';
import { createSlash } from './composer/slash.js';
import { createAttachments } from './composer/attachments.js';
import { createDrafts } from './composer/drafts.js';
import { createLibrary } from './composer/library.js';
import { createWallpapers } from './composer/wallpapers.js';
import { createBlocks } from './composer/blocks.js';

export function createComposer(ctx) {
  const input = createInput({ ...ctx,

  });
  const library = createLibrary({ ...ctx,
    input,
    stash: () => slash.stashPrompt(),
  });
  const wallpapers = createWallpapers(ctx);
  const slash = createSlash({ ...ctx,
    input,
    library,
    wallpapers,
  });
  const attachments = createAttachments({ ...ctx,
    input,
  });
  const drafts = createDrafts({ ...ctx,
    input,
  });
  const blocks = createBlocks({ ...ctx,
    input,
  });
  return {
    ...input, ...slash, ...attachments, ...drafts, ...library, ...wallpapers, ...blocks,
    get dockSpaceLocked() { return input.dockSpaceLocked; },
  };
}
